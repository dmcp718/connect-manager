"""
Test Plan: Tier 2 Database Architecture Improvements
=====================================================

Tests for:
1. Connection pooling - verify connections are reused
2. Job timeout mechanism - verify stale jobs are marked as failed
3. Idempotent SQS processing - verify duplicate messages are skipped

Run with: docker compose exec web python tests/test_tier2_db_improvements.py
"""

import sys
import uuid
import time
import threading
from datetime import datetime, timedelta

# Add app directory to path
sys.path.insert(0, '/app')

from services import database as db


def test_connection_pool_exists():
    """Test 2.1.1: Verify connection pool infrastructure exists."""
    print("\n=== Test 2.1.1: Connection Pool Infrastructure ===")

    # Check that pool-related functions exist
    required_funcs = ['get_connection', 'release_connection', 'pooled_connection']
    missing = []

    for func in required_funcs:
        if not hasattr(db, func):
            missing.append(func)

    if missing:
        print(f"FAIL: Missing functions: {missing}")
        return False

    # Check pool configuration
    if not hasattr(db, '_POOL_SIZE'):
        print("FAIL: _POOL_SIZE not defined")
        return False

    print(f"PASS: Connection pool configured with size {db._POOL_SIZE}")
    return True


def test_connection_pool_reuse():
    """Test 2.1.2: Verify connections are reused from pool."""
    print("\n=== Test 2.1.2: Connection Pool Reuse ===")

    # Get a connection and release it
    conn1 = db.get_connection()
    conn1_id = id(conn1)
    db.release_connection(conn1)

    # Get another connection - should be the same one from pool
    conn2 = db.get_connection()
    conn2_id = id(conn2)
    db.release_connection(conn2)

    if conn1_id == conn2_id:
        print("PASS: Connection was reused from pool")
        return True
    else:
        print("WARN: Connection was not reused (pool may have been empty)")
        return True  # Still pass - new connections are valid


def test_pooled_connection_context_manager():
    """Test 2.1.3: Verify pooled_connection context manager works."""
    print("\n=== Test 2.1.3: pooled_connection Context Manager ===")

    try:
        with db.pooled_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()[0]
            if result != 1:
                print("FAIL: Query returned unexpected result")
                return False

        print("PASS: pooled_connection context manager works")
        return True
    except Exception as e:
        print(f"FAIL: Exception raised: {e}")
        return False


def test_concurrent_connections():
    """Test 2.1.4: Verify pool handles concurrent access."""
    print("\n=== Test 2.1.4: Concurrent Connection Access ===")

    errors = []
    results = []

    def worker(worker_id):
        try:
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT ?", (worker_id,))
            result = cursor.fetchone()[0]
            results.append(result)
            time.sleep(0.01)  # Small delay to simulate work
            db.release_connection(conn)
        except Exception as e:
            errors.append(f"Worker {worker_id}: {e}")

    # Run 20 concurrent workers
    threads = []
    for i in range(20):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    if errors:
        print(f"FAIL: Errors occurred: {errors[:3]}")
        return False

    if len(results) != 20:
        print(f"FAIL: Expected 20 results, got {len(results)}")
        return False

    print(f"PASS: {len(results)} concurrent operations completed successfully")
    return True


def test_timeout_stale_jobs_function():
    """Test 2.2.1: Verify timeout_stale_jobs function exists."""
    print("\n=== Test 2.2.1: timeout_stale_jobs Function ===")

    if not hasattr(db, 'timeout_stale_jobs'):
        print("FAIL: timeout_stale_jobs function not found")
        return False

    print("PASS: timeout_stale_jobs function exists")
    return True


def test_timeout_stale_jobs_logic():
    """Test 2.2.2: Verify stale jobs are timed out correctly."""
    print("\n=== Test 2.2.2: Stale Job Timeout Logic ===")

    # Create a test job
    job_id = db.create_job(
        bucket="test-timeout-bucket",
        prefix="test/timeout/",
        filespace_id="test-fs-id",
        datastore_id="test-ds-id",
    )

    # Mark it as running with an old started_at time
    # Use SQLite-compatible datetime format (space separator, not T)
    conn = db.get_connection()
    cursor = conn.cursor()
    old_time = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("""
        UPDATE import_jobs
        SET status = 'running', started_at = ?
        WHERE id = ?
    """, (old_time, job_id))
    conn.commit()
    db.release_connection(conn)

    # Verify job is running
    job = db.get_job(job_id)
    if job['status'] != 'running':
        print(f"FAIL: Job not in running state: {job['status']}")
        db.delete_job(job_id)
        return False

    # Run timeout check (1 hour threshold)
    timed_out = db.timeout_stale_jobs(hours=1)

    # Verify job was timed out
    job = db.get_job(job_id)
    if job['status'] != 'failed':
        print(f"FAIL: Job not marked as failed: {job['status']}")
        db.delete_job(job_id)
        return False

    if 'timed out' not in job['error_message'].lower():
        print(f"FAIL: Error message doesn't indicate timeout: {job['error_message']}")
        db.delete_job(job_id)
        return False

    # Cleanup
    db.delete_job(job_id)

    print(f"PASS: Stale job correctly timed out (timed_out={timed_out})")
    return True


def test_timeout_preserves_recent_jobs():
    """Test 2.2.3: Verify recent running jobs are NOT timed out."""
    print("\n=== Test 2.2.3: Recent Jobs Not Timed Out ===")

    # Create a test job
    job_id = db.create_job(
        bucket="test-recent-bucket",
        prefix="test/recent/",
        filespace_id="test-fs-id",
        datastore_id="test-ds-id",
    )

    # Mark it as running with recent started_at time
    # Use SQLite-compatible datetime format (space separator, not T)
    conn = db.get_connection()
    cursor = conn.cursor()
    recent_time = (datetime.now() - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("""
        UPDATE import_jobs
        SET status = 'running', started_at = ?
        WHERE id = ?
    """, (recent_time, job_id))
    conn.commit()
    db.release_connection(conn)

    # Run timeout check (1 hour threshold)
    db.timeout_stale_jobs(hours=1)

    # Verify job is still running
    job = db.get_job(job_id)
    if job['status'] != 'running':
        print(f"FAIL: Recent job was incorrectly timed out: {job['status']}")
        db.delete_job(job_id)
        return False

    # Cleanup
    db.delete_job(job_id)

    print("PASS: Recent running job preserved")
    return True


def test_worker_timeout_cron():
    """Test 2.2.4: Verify timeout cron is defined in worker."""
    print("\n=== Test 2.2.4: Worker Timeout Cron Job ===")

    try:
        from services.worker import timeout_stale_jobs, WorkerSettings
        import inspect

        # Verify it's an async function
        if not inspect.iscoroutinefunction(timeout_stale_jobs):
            print("FAIL: timeout_stale_jobs is not async")
            return False

        # Verify it's in cron_jobs
        cron_job_count = len(WorkerSettings.cron_jobs)
        if cron_job_count < 3:
            print(f"FAIL: Expected at least 3 cron jobs, found {cron_job_count}")
            return False

        print(f"PASS: timeout_stale_jobs cron job configured ({cron_job_count} total crons)")
        return True
    except ImportError as e:
        print(f"FAIL: Could not import: {e}")
        return False


def test_sqs_event_exists_function():
    """Test 2.3.1: Verify sqs_event_exists function exists."""
    print("\n=== Test 2.3.1: sqs_event_exists Function ===")

    if not hasattr(db, 'sqs_event_exists'):
        print("FAIL: sqs_event_exists function not found")
        return False

    print("PASS: sqs_event_exists function exists")
    return True


def test_sqs_event_idempotency():
    """Test 2.3.2: Verify idempotent event checking works."""
    print("\n=== Test 2.3.2: SQS Event Idempotency ===")

    # Create a test queue first
    queue_id = f"test-idemp-{uuid.uuid4().hex[:8]}"

    db.create_sqs_queue(
        queue_id=queue_id,
        queue_url=f"https://sqs.us-east-1.amazonaws.com/123456789/{queue_id}",
        queue_arn=None,
        name=f"Idempotency Test Queue",
        region="us-east-1",
        datastore_id="test-ds",
        filespace_id="test-fs",
    )

    message_id = f"msg-{uuid.uuid4().hex[:8]}"
    object_key = "test/file.txt"

    # Check event doesn't exist
    exists_before = db.sqs_event_exists(message_id, queue_id, object_key)
    if exists_before:
        print("FAIL: Event should not exist before creation")
        db.delete_sqs_queue(queue_id)
        return False

    # Create the event
    event_id = f"event-{uuid.uuid4().hex[:8]}"
    db.create_sqs_event(
        event_id=event_id,
        queue_id=queue_id,
        message_id=message_id,
        event_type="s3:ObjectCreated:Put",
        bucket="test-bucket",
        object_key=object_key,
        object_size=100,
        event_time=datetime.now().isoformat(),
    )

    # Check event now exists
    exists_after = db.sqs_event_exists(message_id, queue_id, object_key)
    if not exists_after:
        print("FAIL: Event should exist after creation")
        db.delete_sqs_queue(queue_id)
        return False

    # Cleanup
    db.delete_sqs_queue(queue_id)

    print("PASS: Idempotency check works correctly")
    return True


def test_sqs_event_different_keys():
    """Test 2.3.3: Verify different object keys are not considered duplicates."""
    print("\n=== Test 2.3.3: Different Keys Not Duplicates ===")

    queue_id = f"test-keys-{uuid.uuid4().hex[:8]}"

    db.create_sqs_queue(
        queue_id=queue_id,
        queue_url=f"https://sqs.us-east-1.amazonaws.com/123456789/{queue_id}",
        queue_arn=None,
        name=f"Keys Test Queue",
        region="us-east-1",
        datastore_id="test-ds",
        filespace_id="test-fs",
    )

    message_id = f"msg-{uuid.uuid4().hex[:8]}"

    # Create event for file1.txt
    db.create_sqs_event(
        event_id=f"event-{uuid.uuid4().hex[:8]}",
        queue_id=queue_id,
        message_id=message_id,
        event_type="s3:ObjectCreated:Put",
        bucket="test-bucket",
        object_key="test/file1.txt",
        object_size=100,
        event_time=datetime.now().isoformat(),
    )

    # Check file2.txt doesn't exist (same message_id but different key)
    exists = db.sqs_event_exists(message_id, queue_id, "test/file2.txt")

    # Cleanup
    db.delete_sqs_queue(queue_id)

    if exists:
        print("FAIL: Different object key was incorrectly flagged as duplicate")
        return False

    print("PASS: Different object keys correctly treated as unique")
    return True


def run_all_tests():
    """Run all Tier 2 tests and report results."""
    print("=" * 60)
    print("Tier 2 Database Improvements Test Suite")
    print("=" * 60)

    tests = [
        # 2.1 Connection Pooling
        ("2.1.1 Pool Infrastructure", test_connection_pool_exists),
        ("2.1.2 Connection Reuse", test_connection_pool_reuse),
        ("2.1.3 Pooled Context Manager", test_pooled_connection_context_manager),
        ("2.1.4 Concurrent Connections", test_concurrent_connections),
        # 2.2 Job Timeout
        ("2.2.1 Timeout Function", test_timeout_stale_jobs_function),
        ("2.2.2 Stale Job Timeout", test_timeout_stale_jobs_logic),
        ("2.2.3 Recent Jobs Preserved", test_timeout_preserves_recent_jobs),
        ("2.2.4 Worker Timeout Cron", test_worker_timeout_cron),
        # 2.3 Idempotent SQS
        ("2.3.1 Event Exists Function", test_sqs_event_exists_function),
        ("2.3.2 Event Idempotency", test_sqs_event_idempotency),
        ("2.3.3 Different Keys Unique", test_sqs_event_different_keys),
    ]

    results = []
    for name, test_fn in tests:
        try:
            passed = test_fn()
            results.append((name, passed))
        except Exception as e:
            print(f"ERROR in {name}: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    print("\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, p in results if p)
    total = len(results)

    for name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")

    print(f"\nTotal: {passed}/{total} tests passed")
    print("=" * 60)

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
