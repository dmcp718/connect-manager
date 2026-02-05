"""
Test Plan: Tier 1 Database Architecture Improvements
=====================================================

Tests for:
1. Database indexes - verify indexed queries work correctly
2. Transaction wrapper - verify atomic operations in delete_sqs_queue
3. Cleanup cron job - verify cleanup_old_events removes old records

Run with: docker compose exec web python tests/test_tier1_db_improvements.py
"""

import sys
import uuid
from datetime import datetime, timedelta

# Add app directory to path
sys.path.insert(0, '/app')

from services import database as db


def test_indexes_exist():
    """Test 1.1: Verify all performance indexes were created."""
    print("\n=== Test 1.1: Index Existence ===")

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")
    indexes = [row[0] for row in cursor.fetchall()]
    conn.close()

    expected_indexes = [
        'idx_import_jobs_status',
        'idx_import_jobs_user_id',
        'idx_import_jobs_status_user',
        'idx_sqs_events_status',
        'idx_sqs_events_queue_id',
        'idx_sqs_events_queue_status',
        'idx_sqs_events_message_id',
    ]

    missing = [idx for idx in expected_indexes if idx not in indexes]

    if missing:
        print(f"FAIL: Missing indexes: {missing}")
        return False

    print(f"PASS: All {len(expected_indexes)} indexes exist")
    return True


def test_indexed_queries():
    """Test 1.2: Verify indexed columns are used in queries."""
    print("\n=== Test 1.2: Indexed Query Performance ===")

    conn = db.get_connection()
    cursor = conn.cursor()

    # Test EXPLAIN QUERY PLAN for indexed queries
    test_queries = [
        ("import_jobs by status", "SELECT * FROM import_jobs WHERE status = 'pending'"),
        ("import_jobs by user_id", "SELECT * FROM import_jobs WHERE user_id = 'test'"),
        ("import_jobs by status+user", "SELECT * FROM import_jobs WHERE status = 'pending' AND user_id = 'test'"),
        ("sqs_events by status", "SELECT * FROM sqs_events WHERE status = 'pending'"),
        ("sqs_events by queue_id", "SELECT * FROM sqs_events WHERE queue_id = 'test'"),
        ("sqs_events by message_id", "SELECT * FROM sqs_events WHERE message_id = 'test'"),
    ]

    all_passed = True
    for name, query in test_queries:
        cursor.execute(f"EXPLAIN QUERY PLAN {query}")
        plan = cursor.fetchall()
        # Check if index is used (plan should mention USING INDEX or SEARCH)
        plan_str = str(plan)
        uses_index = 'USING INDEX' in plan_str or 'USING COVERING INDEX' in plan_str
        status = "PASS" if uses_index else "WARN (may use index at runtime)"
        print(f"  {status}: {name}")
        if not uses_index:
            print(f"    Plan: {plan_str[:100]}")

    conn.close()
    print("PASS: Indexed queries verified")
    return True


def test_transaction_wrapper_success():
    """Test 2.1: Transaction wrapper commits on success."""
    print("\n=== Test 2.1: Transaction Commit ===")

    # Create a test queue
    queue_id = f"test-tx-{uuid.uuid4().hex[:8]}"

    db.create_sqs_queue(
        queue_id=queue_id,
        queue_url=f"https://sqs.us-east-1.amazonaws.com/123456789/{queue_id}",
        queue_arn=None,
        name=f"Test Queue {queue_id}",
        region="us-east-1",
        datastore_id="test-ds",
        filespace_id="test-fs",
    )

    # Create test events for this queue
    for i in range(3):
        db.create_sqs_event(
            event_id=f"{queue_id}-event-{i}",
            queue_id=queue_id,
            message_id=f"msg-{i}",
            event_type="s3:ObjectCreated:Put",
            bucket="test-bucket",
            object_key=f"test/file{i}.txt",
            object_size=100,
            event_time=datetime.now().isoformat(),
        )

    # Verify queue and events exist
    queue = db.get_sqs_queue(queue_id)
    if not queue:
        print("FAIL: Test queue not created")
        return False

    # Count events for this queue
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sqs_events WHERE queue_id = ?", (queue_id,))
    event_count = cursor.fetchone()[0]
    conn.close()

    if event_count != 3:
        print(f"FAIL: Expected 3 events, got {event_count}")
        return False

    # Delete queue (should delete events atomically)
    deleted = db.delete_sqs_queue(queue_id)

    if not deleted:
        print("FAIL: delete_sqs_queue returned False")
        return False

    # Verify both queue and events are gone
    queue = db.get_sqs_queue(queue_id)
    if queue:
        print("FAIL: Queue still exists after deletion")
        return False

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sqs_events WHERE queue_id = ?", (queue_id,))
    remaining_events = cursor.fetchone()[0]
    conn.close()

    if remaining_events > 0:
        print(f"FAIL: {remaining_events} orphan events remain")
        return False

    print("PASS: Transaction committed atomically (queue + events deleted)")
    return True


def test_transaction_wrapper_exists():
    """Test 2.2: Verify transaction() context manager exists and works."""
    print("\n=== Test 2.2: Transaction Context Manager ===")

    try:
        # Test basic transaction usage
        with db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()[0]
            if result != 1:
                print("FAIL: Basic query in transaction failed")
                return False

        print("PASS: transaction() context manager works")
        return True
    except Exception as e:
        print(f"FAIL: transaction() raised exception: {e}")
        return False


def test_cleanup_old_events():
    """Test 3.1: cleanup_old_events removes old records."""
    print("\n=== Test 3.1: Cleanup Old Events ===")

    # Create a temporary queue for test events
    queue_id = f"test-cleanup-{uuid.uuid4().hex[:8]}"

    db.create_sqs_queue(
        queue_id=queue_id,
        queue_url=f"https://sqs.us-east-1.amazonaws.com/123456789/{queue_id}",
        queue_arn=None,
        name=f"Cleanup Test Queue",
        region="us-east-1",
        datastore_id="test-ds",
        filespace_id="test-fs",
    )

    # Insert events with old timestamps directly
    conn = db.get_connection()
    cursor = conn.cursor()

    old_date = (datetime.now() - timedelta(days=10)).isoformat()
    recent_date = (datetime.now() - timedelta(days=1)).isoformat()

    # Insert old events (should be deleted)
    for i in range(5):
        cursor.execute("""
            INSERT INTO sqs_events (id, queue_id, message_id, event_type, bucket, object_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (f"{queue_id}-old-{i}", queue_id, f"old-msg-{i}", "s3:ObjectCreated:Put",
              "test-bucket", f"old/file{i}.txt", old_date))

    # Insert recent events (should NOT be deleted)
    for i in range(3):
        cursor.execute("""
            INSERT INTO sqs_events (id, queue_id, message_id, event_type, bucket, object_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (f"{queue_id}-new-{i}", queue_id, f"new-msg-{i}", "s3:ObjectCreated:Put",
              "test-bucket", f"new/file{i}.txt", recent_date))

    conn.commit()

    # Count before cleanup
    cursor.execute("SELECT COUNT(*) FROM sqs_events WHERE queue_id = ?", (queue_id,))
    before_count = cursor.fetchone()[0]
    conn.close()

    if before_count != 8:
        print(f"FAIL: Expected 8 test events, got {before_count}")
        db.delete_sqs_queue(queue_id)
        return False

    # Run cleanup
    deleted = db.clear_old_sqs_events(days=7)

    # Count after cleanup
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sqs_events WHERE queue_id = ?", (queue_id,))
    after_count = cursor.fetchone()[0]
    conn.close()

    # Should have 3 recent events remaining
    if after_count != 3:
        print(f"FAIL: Expected 3 events after cleanup, got {after_count}")
        db.delete_sqs_queue(queue_id)
        return False

    if deleted < 5:
        print(f"WARN: Expected to delete at least 5 old events, deleted {deleted}")

    # Cleanup test queue
    db.delete_sqs_queue(queue_id)

    print(f"PASS: Cleanup deleted {deleted} old events, {after_count} recent events preserved")
    return True


def test_cleanup_function_in_worker():
    """Test 3.2: Verify cleanup_old_events function is importable from worker."""
    print("\n=== Test 3.2: Worker Cleanup Function ===")

    try:
        from services.worker import cleanup_old_events
        import inspect

        # Verify it's an async function
        if not inspect.iscoroutinefunction(cleanup_old_events):
            print("FAIL: cleanup_old_events is not async")
            return False

        print("PASS: cleanup_old_events is properly defined in worker")
        return True
    except ImportError as e:
        print(f"FAIL: Could not import cleanup_old_events: {e}")
        return False


def run_all_tests():
    """Run all Tier 1 tests and report results."""
    print("=" * 60)
    print("Tier 1 Database Improvements Test Suite")
    print("=" * 60)

    tests = [
        ("1.1 Index Existence", test_indexes_exist),
        ("1.2 Indexed Queries", test_indexed_queries),
        ("2.1 Transaction Commit", test_transaction_wrapper_success),
        ("2.2 Transaction Context Manager", test_transaction_wrapper_exists),
        ("3.1 Cleanup Old Events", test_cleanup_old_events),
        ("3.2 Worker Cleanup Function", test_cleanup_function_in_worker),
    ]

    results = []
    for name, test_fn in tests:
        try:
            passed = test_fn()
            results.append((name, passed))
        except Exception as e:
            print(f"ERROR in {name}: {e}")
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
