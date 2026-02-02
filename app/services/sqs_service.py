"""
SQS Service for receiving S3 event notifications.
Handles SQS queue creation, S3 bucket notification setup, message receiving, and deletion.
"""

import json
import re
from typing import Optional, List, Dict, Any
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError


class S3NotificationService:
    """Service for configuring S3 bucket event notifications."""

    def __init__(self, access_key: str, secret_key: str, region: str):
        """Initialize S3 client with credentials."""
        self.client = boto3.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        self.region = region

    def get_bucket_notification_configuration(self, bucket_name: str) -> Dict[str, Any]:
        """Get current notification configuration for a bucket."""
        try:
            response = self.client.get_bucket_notification_configuration(Bucket=bucket_name)
            # Remove ResponseMetadata
            response.pop('ResponseMetadata', None)
            return response
        except ClientError as e:
            raise S3NotificationError(f"Failed to get bucket notifications: {e}")

    def add_sqs_notification(
        self,
        bucket_name: str,
        queue_arn: str,
        notification_id: str = "LucidLinkAutoImport",
        events: List[str] = None,
        prefix: str = "",
        suffix: str = "",
    ) -> bool:
        """
        Add SQS notification to an S3 bucket for object creation events.

        Args:
            bucket_name: The S3 bucket name
            queue_arn: The SQS queue ARN to receive notifications
            notification_id: Unique ID for this notification configuration
            events: List of S3 events to trigger on (default: all object created events)
            prefix: Optional key prefix filter
            suffix: Optional key suffix filter

        Returns:
            True if successful
        """
        if events is None:
            events = ["s3:ObjectCreated:*"]

        try:
            # Get existing configuration
            current_config = self.get_bucket_notification_configuration(bucket_name)

            # Get existing queue configurations
            queue_configs = current_config.get('QueueConfigurations', [])

            # Check if we already have a config with this ID or queue ARN
            queue_configs = [
                cfg for cfg in queue_configs
                if cfg.get('Id') != notification_id and cfg.get('QueueArn') != queue_arn
            ]

            # Build filter rules if prefix or suffix specified
            filter_rules = []
            if prefix:
                filter_rules.append({'Name': 'prefix', 'Value': prefix})
            if suffix:
                filter_rules.append({'Name': 'suffix', 'Value': suffix})

            # Create new notification config
            new_config = {
                'Id': notification_id,
                'QueueArn': queue_arn,
                'Events': events,
            }

            if filter_rules:
                new_config['Filter'] = {
                    'Key': {
                        'FilterRules': filter_rules
                    }
                }

            queue_configs.append(new_config)

            # Build full configuration (preserve other notification types)
            full_config = {}
            if queue_configs:
                full_config['QueueConfigurations'] = queue_configs
            if current_config.get('TopicConfigurations'):
                full_config['TopicConfigurations'] = current_config['TopicConfigurations']
            if current_config.get('LambdaFunctionConfigurations'):
                full_config['LambdaFunctionConfigurations'] = current_config['LambdaFunctionConfigurations']
            if current_config.get('EventBridgeConfiguration'):
                full_config['EventBridgeConfiguration'] = current_config['EventBridgeConfiguration']

            # Apply configuration
            self.client.put_bucket_notification_configuration(
                Bucket=bucket_name,
                NotificationConfiguration=full_config,
            )

            return True

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            error_msg = e.response.get('Error', {}).get('Message', str(e))

            if 'InvalidArgument' in error_code or 'unable to validate' in error_msg.lower():
                raise S3NotificationError(
                    f"S3 cannot send to SQS queue. Ensure the queue policy allows s3:SendMessage from this bucket. Error: {error_msg}"
                )
            raise S3NotificationError(f"Failed to configure bucket notifications: {error_msg}")

    def remove_sqs_notification(self, bucket_name: str, queue_arn: str) -> bool:
        """
        Remove SQS notification from an S3 bucket.

        Args:
            bucket_name: The S3 bucket name
            queue_arn: The SQS queue ARN to remove

        Returns:
            True if successful
        """
        try:
            current_config = self.get_bucket_notification_configuration(bucket_name)

            # Filter out the queue ARN
            queue_configs = current_config.get('QueueConfigurations', [])
            queue_configs = [cfg for cfg in queue_configs if cfg.get('QueueArn') != queue_arn]

            # Build full configuration
            full_config = {}
            if queue_configs:
                full_config['QueueConfigurations'] = queue_configs
            if current_config.get('TopicConfigurations'):
                full_config['TopicConfigurations'] = current_config['TopicConfigurations']
            if current_config.get('LambdaFunctionConfigurations'):
                full_config['LambdaFunctionConfigurations'] = current_config['LambdaFunctionConfigurations']
            if current_config.get('EventBridgeConfiguration'):
                full_config['EventBridgeConfiguration'] = current_config['EventBridgeConfiguration']

            # Apply configuration (empty dict clears all if nothing left)
            self.client.put_bucket_notification_configuration(
                Bucket=bucket_name,
                NotificationConfiguration=full_config if full_config else {},
            )

            return True

        except ClientError as e:
            raise S3NotificationError(f"Failed to remove bucket notification: {e}")


class S3NotificationError(Exception):
    """Custom exception for S3 notification errors."""
    pass


class SQSService:
    """Service for interacting with AWS SQS queues."""

    def __init__(self, access_key: str, secret_key: str, region: str):
        """Initialize SQS client with credentials."""
        self.client = boto3.client(
            'sqs',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        self.region = region

    def receive_messages(
        self,
        queue_url: str,
        max_messages: int = 10,
        wait_time_seconds: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Receive messages from SQS queue.

        Args:
            queue_url: The SQS queue URL
            max_messages: Maximum number of messages to receive (1-10)
            wait_time_seconds: Long polling wait time (0-20 seconds)

        Returns:
            List of messages with Body, MessageId, ReceiptHandle
        """
        try:
            response = self.client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=min(max_messages, 10),
                WaitTimeSeconds=wait_time_seconds,
                AttributeNames=['All'],
                MessageAttributeNames=['All'],
            )
            return response.get('Messages', [])
        except ClientError as e:
            raise SQSError(f"Failed to receive messages: {e}")

    def delete_message(self, queue_url: str, receipt_handle: str) -> bool:
        """
        Delete a processed message from the queue.

        Args:
            queue_url: The SQS queue URL
            receipt_handle: The receipt handle from the received message

        Returns:
            True if deleted successfully
        """
        try:
            self.client.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
            )
            return True
        except ClientError as e:
            raise SQSError(f"Failed to delete message: {e}")

    def get_queue_attributes(self, queue_url: str) -> Dict[str, Any]:
        """
        Get queue attributes like message count, ARN, etc.

        Args:
            queue_url: The SQS queue URL

        Returns:
            Dictionary of queue attributes
        """
        try:
            response = self.client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=['All'],
            )
            return response.get('Attributes', {})
        except ClientError as e:
            raise SQSError(f"Failed to get queue attributes: {e}")

    def validate_queue_url(self, queue_url: str) -> Dict[str, str]:
        """
        Validate a queue URL and extract info.

        Args:
            queue_url: The SQS queue URL

        Returns:
            Dictionary with queue info (url, arn, name, region)
        """
        try:
            attrs = self.get_queue_attributes(queue_url)
            arn = attrs.get('QueueArn', '')

            # Extract name from ARN (arn:aws:sqs:region:account:name)
            name = arn.split(':')[-1] if arn else queue_url.split('/')[-1]

            # Extract region from ARN
            arn_parts = arn.split(':')
            region = arn_parts[3] if len(arn_parts) > 3 else self.region

            return {
                'url': queue_url,
                'arn': arn,
                'name': name,
                'region': region,
                'approximate_messages': attrs.get('ApproximateNumberOfMessages', '0'),
            }
        except ClientError as e:
            raise SQSError(f"Failed to validate queue: {e}")

    def create_queue(
        self,
        queue_name: str,
        visibility_timeout: int = 300,
        message_retention: int = 345600,  # 4 days
    ) -> Dict[str, str]:
        """
        Create a new SQS queue.

        Args:
            queue_name: Name for the new queue
            visibility_timeout: Seconds a message is hidden after being received (default 5 min)
            message_retention: Seconds to retain messages (default 4 days)

        Returns:
            Dictionary with queue info (url, arn, name, region)
        """
        try:
            response = self.client.create_queue(
                QueueName=queue_name,
                Attributes={
                    'VisibilityTimeout': str(visibility_timeout),
                    'MessageRetentionPeriod': str(message_retention),
                },
            )
            queue_url = response['QueueUrl']

            # Get full attributes including ARN
            return self.validate_queue_url(queue_url)

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == 'QueueAlreadyExists':
                raise SQSError(f"Queue '{queue_name}' already exists")
            raise SQSError(f"Failed to create queue: {e}")

    def list_queues(self, prefix: str = "") -> List[Dict[str, str]]:
        """
        List all SQS queues in the current region.

        Args:
            prefix: Optional queue name prefix to filter results

        Returns:
            List of dictionaries with queue info (url, name)
        """
        try:
            params = {}
            if prefix:
                params['QueueNamePrefix'] = prefix

            response = self.client.list_queues(**params)
            queue_urls = response.get('QueueUrls', [])

            queues = []
            for url in queue_urls:
                # Extract queue name from URL
                # URL format: https://sqs.region.amazonaws.com/account-id/queue-name
                name = url.split('/')[-1]
                queues.append({
                    'url': url,
                    'name': name,
                })

            return queues

        except ClientError as e:
            raise SQSError(f"Failed to list queues: {e}")

    def get_queue_policy_for_s3(self, queue_arn: str, bucket_name: str, account_id: str) -> str:
        """
        Generate an SQS policy that allows S3 to send notifications.

        Args:
            queue_arn: The SQS queue ARN
            bucket_name: The S3 bucket name
            account_id: AWS account ID

        Returns:
            JSON policy string
        """
        policy = {
            "Version": "2012-10-17",
            "Id": "S3NotificationPolicy",
            "Statement": [
                {
                    "Sid": "AllowS3ToSendMessage",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "s3.amazonaws.com"
                    },
                    "Action": "sqs:SendMessage",
                    "Resource": queue_arn,
                    "Condition": {
                        "ArnLike": {
                            "aws:SourceArn": f"arn:aws:s3:*:*:{bucket_name}"
                        },
                        "StringEquals": {
                            "aws:SourceAccount": account_id
                        }
                    }
                }
            ]
        }
        return json.dumps(policy)

    def set_queue_policy(self, queue_url: str, policy: str) -> bool:
        """
        Set the access policy for an SQS queue.

        Args:
            queue_url: The SQS queue URL
            policy: JSON policy string

        Returns:
            True if successful
        """
        try:
            self.client.set_queue_attributes(
                QueueUrl=queue_url,
                Attributes={
                    'Policy': policy,
                }
            )
            return True
        except ClientError as e:
            raise SQSError(f"Failed to set queue policy: {e}")

    def configure_queue_for_s3(self, queue_url: str, bucket_name: str) -> bool:
        """
        Configure an SQS queue to receive S3 event notifications.

        Args:
            queue_url: The SQS queue URL
            bucket_name: The S3 bucket that will send notifications

        Returns:
            True if successful
        """
        try:
            # Get queue ARN and account ID
            attrs = self.get_queue_attributes(queue_url)
            queue_arn = attrs.get('QueueArn', '')

            if not queue_arn:
                raise SQSError("Could not get queue ARN")

            # Extract account ID from ARN
            arn_parts = queue_arn.split(':')
            account_id = arn_parts[4] if len(arn_parts) > 4 else ''

            if not account_id:
                raise SQSError("Could not determine AWS account ID")

            # Generate and set policy
            policy = self.get_queue_policy_for_s3(queue_arn, bucket_name, account_id)
            return self.set_queue_policy(queue_url, policy)

        except ClientError as e:
            raise SQSError(f"Failed to configure queue for S3: {e}")

    def delete_queue(self, queue_url: str) -> bool:
        """
        Delete an SQS queue.

        Args:
            queue_url: The SQS queue URL

        Returns:
            True if deleted successfully
        """
        try:
            self.client.delete_queue(QueueUrl=queue_url)
            return True
        except ClientError as e:
            raise SQSError(f"Failed to delete queue: {e}")

    @staticmethod
    def parse_s3_events(message_body: str) -> List[Dict[str, Any]]:
        """
        Parse S3 event notifications from SQS message body.

        S3 events come in this format:
        {
            "Records": [
                {
                    "eventSource": "aws:s3",
                    "eventName": "ObjectCreated:Put",
                    "s3": {
                        "bucket": {"name": "my-bucket"},
                        "object": {"key": "path/to/file.mp4", "size": 1048576}
                    },
                    "eventTime": "2024-01-15T10:30:00.000Z"
                }
            ]
        }

        Args:
            message_body: The SQS message body (JSON string)

        Returns:
            List of parsed S3 events
        """
        events = []

        try:
            data = json.loads(message_body)

            # Handle SNS-wrapped messages (S3 -> SNS -> SQS)
            if 'Message' in data and isinstance(data.get('Message'), str):
                # SNS wraps the S3 event in a "Message" field
                data = json.loads(data['Message'])

            records = data.get('Records', [])
            for record in records:
                # Only process S3 events
                if record.get('eventSource') != 'aws:s3':
                    continue

                event_name = record.get('eventName', '')
                s3_info = record.get('s3', {})
                bucket_info = s3_info.get('bucket', {})
                object_info = s3_info.get('object', {})

                # URL-decode the object key (S3 URL-encodes special chars)
                object_key = unquote_plus(object_info.get('key', ''))

                # Skip test events
                if event_name == 's3:TestEvent' or object_key == '':
                    continue

                events.append({
                    'event_type': event_name,
                    'bucket': bucket_info.get('name', ''),
                    'key': object_key,
                    'size': object_info.get('size'),
                    'event_time': record.get('eventTime'),
                    'aws_region': record.get('awsRegion'),
                })

        except json.JSONDecodeError:
            # Not a JSON message - might be a test message
            pass

        return events

    @staticmethod
    def is_create_event(event_type: str) -> bool:
        """Check if event type is an object creation event."""
        create_patterns = [
            'ObjectCreated:Put',
            'ObjectCreated:Post',
            'ObjectCreated:Copy',
            'ObjectCreated:CompleteMultipartUpload',
        ]
        return event_type in create_patterns

    @staticmethod
    def queue_url_from_arn(arn: str) -> Optional[str]:
        """
        Convert an SQS ARN to a queue URL.

        ARN format: arn:aws:sqs:region:account-id:queue-name
        URL format: https://sqs.region.amazonaws.com/account-id/queue-name
        """
        match = re.match(r'arn:aws:sqs:([^:]+):([^:]+):(.+)', arn)
        if match:
            region, account_id, queue_name = match.groups()
            return f"https://sqs.{region}.amazonaws.com/{account_id}/{queue_name}"
        return None

    @staticmethod
    def normalize_queue_input(input_str: str) -> Optional[str]:
        """
        Normalize queue input (URL or ARN) to a queue URL.

        Args:
            input_str: Either a queue URL or ARN

        Returns:
            Queue URL or None if invalid
        """
        input_str = input_str.strip()

        # Already a URL
        if input_str.startswith('https://'):
            return input_str

        # ARN format
        if input_str.startswith('arn:aws:sqs:'):
            return SQSService.queue_url_from_arn(input_str)

        return None


class SQSError(Exception):
    """Custom exception for SQS errors."""
    pass
