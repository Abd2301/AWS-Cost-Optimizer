import boto3
import json
from datetime import datetime, timezone
from typing import List, Dict

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('CostOptimizationLog')
ec2 = boto3.client('ec2')
sns = boto3.client('sns')

# Configuration
SNS_TOPIC_ARN = 'arn:aws:sns:ap-south-1:574337396853:cost-alerts-topic'  # UPDATE THIS
TAG_KEY = 'CostOptimization'
TAG_VALUE_PREFIX = 'DeleteAfter-'
DRY_RUN = False  # Set to True to test without actually deleting

def lambda_handler(event, context):
    """
    Main Lambda function to clean up expired resources
    """
    print("=" * 60)
    print("STARTING RESOURCE CLEANUP SCAN")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"DRY RUN MODE: {DRY_RUN}")
    print("=" * 60)
    
    cleanup_results = {
        'volumes_deleted': [],
        'snapshots_deleted': [],
        'eips_released': [],
        'volumes_skipped': [],
        'snapshots_skipped': [],
        'total_savings_monthly': 0.0
    }
    
    # Find and clean up expired resources
    cleanup_results = cleanup_expired_volumes(cleanup_results)
    cleanup_results = cleanup_expired_snapshots(cleanup_results)
    cleanup_results = cleanup_expired_eips(cleanup_results)
    
    log_deletions_to_dynamodb(cleanup_results)
    
    # Send report
    send_cleanup_report(cleanup_results)
    
    print("=" * 60)
    print("CLEANUP COMPLETE")
    print("=" * 60)
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Cleanup complete',
            'deleted_count': len(cleanup_results['volumes_deleted']) + 
                           len(cleanup_results['snapshots_deleted']) + 
                           len(cleanup_results['eips_released']),
            'savings': cleanup_results['total_savings_monthly']
        }, default=str)
    }

def cleanup_expired_volumes(results: Dict) -> Dict:
    """
    Find and delete EBS volumes with expired grace period
    """
    print("\n--- SCANNING VOLUMES ---")
    
    try:
        # Get volumes tagged for deletion
        response = ec2.describe_volumes(
            Filters=[
                {'Name': f'tag:{TAG_KEY}', 'Values': [f'{TAG_VALUE_PREFIX}*']}
            ]
        )
        
        today = datetime.now(timezone.utc).date()
        
        for volume in response['Volumes']:
            volume_id = volume['VolumeId']
            size_gb = volume['Size']
            volume_type = volume['VolumeType']
            
            # Get deletion date from tags
            delete_after_date = None
            for tag in volume.get('Tags', []):
                if tag['Key'] == TAG_KEY:
                    # Extract date from "DeleteAfter-2025-02-09"
                    date_str = tag['Value'].replace(TAG_VALUE_PREFIX, '')
                    try:
                        delete_after_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    except ValueError:
                        print(f"⚠️  Invalid date format for {volume_id}: {tag['Value']}")
                        continue
            
            if not delete_after_date:
                continue
            
            # Check if grace period has expired
            if today >= delete_after_date:
                # Calculate savings
                price_per_gb = {
                    'gp2': 0.114, 'gp3': 0.091, 'io1': 0.143,
                    'io2': 0.143, 'sc1': 0.029, 'st1': 0.051,
                    'standard': 0.057
                }
                monthly_cost = size_gb * price_per_gb.get(volume_type, 0.10)
                
                # Check if still unattached
                if len(volume['Attachments']) > 0:
                    print(f"⏭️  SKIPPED {volume_id}: Now attached to instance")
                    results['volumes_skipped'].append({
                        'volume_id': volume_id,
                        'reason': 'attached_to_instance'
                    })
                    continue
                
                # Create snapshot before deletion (safety)
                snapshot_id = None
                if not DRY_RUN:
                    try:
                        snapshot_response = ec2.create_snapshot(
                            VolumeId=volume_id,
                            Description=f"Pre-deletion snapshot of {volume_id} by automated cleanup",
                            TagSpecifications=[
                                {
                                    'ResourceType': 'snapshot',
                                    'Tags': [
                                        {'Key': 'Name', 'Value': f'AutoCleanup-{volume_id}'},
                                        {'Key': 'OriginalVolumeId', 'Value': volume_id},
                                        {'Key': 'AutomatedBy', 'Value': 'ResourceCleanup'},
                                        {'Key': 'CreatedDate', 'Value': today.isoformat()}
                                    ]
                                }
                            ]
                        )
                        snapshot_id = snapshot_response['SnapshotId']
                        print(f"📸 Created safety snapshot: {snapshot_id}")
                    except Exception as e:
                        print(f"❌ ERROR creating snapshot for {volume_id}: {str(e)}")
                        results['volumes_skipped'].append({
                            'volume_id': volume_id,
                            'reason': f'snapshot_failed: {str(e)}'
                        })
                        continue
                
                # Delete the volume
                if DRY_RUN:
                    print(f"🧪 DRY RUN: Would delete {volume_id} ({size_gb}GB {volume_type}) - ${monthly_cost:.2f}/month")
                else:
                    try:
                        ec2.delete_volume(VolumeId=volume_id)
                        print(f"✅ DELETED {volume_id} ({size_gb}GB {volume_type}) - ${monthly_cost:.2f}/month saved")
                        
                        results['volumes_deleted'].append({
                            'volume_id': volume_id,
                            'size_gb': size_gb,
                            'volume_type': volume_type,
                            'monthly_savings': monthly_cost,
                            'snapshot_id': snapshot_id,
                            'deleted_date': today.isoformat()
                        })
                        results['total_savings_monthly'] += monthly_cost
                        
                    except Exception as e:
                        print(f"❌ ERROR deleting {volume_id}: {str(e)}")
                        results['volumes_skipped'].append({
                            'volume_id': volume_id,
                            'reason': f'deletion_failed: {str(e)}'
                        })
            else:
                days_remaining = (delete_after_date - today).days
                print(f"⏰ Grace period active for {volume_id}: {days_remaining} days remaining")
        
        print(f"\nVolumes deleted: {len(results['volumes_deleted'])}")
        print(f"Volumes skipped: {len(results['volumes_skipped'])}")
        
    except Exception as e:
        print(f"ERROR in volume cleanup: {str(e)}")
    
    return results

def cleanup_expired_snapshots(results: Dict) -> Dict:
    """
    Find and delete snapshots with expired grace period
    """
    print("\n--- SCANNING SNAPSHOTS ---")
    
    try:
        response = ec2.describe_snapshots(
            OwnerIds=['self'],
            Filters=[
                {'Name': f'tag:{TAG_KEY}', 'Values': [f'{TAG_VALUE_PREFIX}*']}
            ]
        )
        
        today = datetime.now(timezone.utc).date()
        
        for snapshot in response['Snapshots']:
            snapshot_id = snapshot['SnapshotId']
            size_gb = snapshot['VolumeSize']
            
            # Get deletion date
            delete_after_date = None
            for tag in snapshot.get('Tags', []):
                if tag['Key'] == TAG_KEY:
                    date_str = tag['Value'].replace(TAG_VALUE_PREFIX, '')
                    try:
                        delete_after_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    except ValueError:
                        continue
            
            if not delete_after_date:
                continue
            
            if today >= delete_after_date:
                monthly_cost = size_gb * 0.057  # Snapshot pricing in ap-south-1
                
                if DRY_RUN:
                    print(f"🧪 DRY RUN: Would delete snapshot {snapshot_id} ({size_gb}GB) - ${monthly_cost:.2f}/month")
                else:
                    try:
                        ec2.delete_snapshot(SnapshotId=snapshot_id)
                        print(f"✅ DELETED snapshot {snapshot_id} ({size_gb}GB) - ${monthly_cost:.2f}/month saved")
                        
                        results['snapshots_deleted'].append({
                            'snapshot_id': snapshot_id,
                            'size_gb': size_gb,
                            'monthly_savings': monthly_cost,
                            'deleted_date': today.isoformat()
                        })
                        results['total_savings_monthly'] += monthly_cost
                        
                    except Exception as e:
                        print(f"❌ ERROR deleting snapshot {snapshot_id}: {str(e)}")
                        results['snapshots_skipped'].append({
                            'snapshot_id': snapshot_id,
                            'reason': str(e)
                        })
        
        print(f"\nSnapshots deleted: {len(results['snapshots_deleted'])}")
        
    except Exception as e:
        print(f"ERROR in snapshot cleanup: {str(e)}")
    
    return results

def cleanup_expired_eips(results: Dict) -> Dict:
    """
    Find and release Elastic IPs with expired grace period
    """
    print("\n--- SCANNING ELASTIC IPs ---")
    
    try:
        response = ec2.describe_addresses(
            Filters=[
                {'Name': f'tag:{TAG_KEY}', 'Values': [f'{TAG_VALUE_PREFIX}*']}
            ]
        )
        
        today = datetime.now(timezone.utc).date()
        
        for address in response['Addresses']:
            allocation_id = address['AllocationId']
            public_ip = address['PublicIp']
            
            # Get deletion date
            delete_after_date = None
            for tag in address.get('Tags', []):
                if tag['Key'] == TAG_KEY:
                    date_str = tag['Value'].replace(TAG_VALUE_PREFIX, '')
                    try:
                        delete_after_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    except ValueError:
                        continue
            
            if not delete_after_date:
                continue
            
            if today >= delete_after_date:
                # Check if now associated (someone attached it)
                if 'AssociationId' in address:
                    print(f"⏭️  SKIPPED {allocation_id} ({public_ip}): Now associated with instance")
                    continue
                
                monthly_cost = 0.005 * 24 * 30  # ~$3.60/month
                
                if DRY_RUN:
                    print(f"🧪 DRY RUN: Would release EIP {public_ip} ({allocation_id}) - ${monthly_cost:.2f}/month")
                else:
                    try:
                        ec2.release_address(AllocationId=allocation_id)
                        print(f"✅ RELEASED EIP {public_ip} ({allocation_id}) - ${monthly_cost:.2f}/month saved")
                        
                        results['eips_released'].append({
                            'allocation_id': allocation_id,
                            'public_ip': public_ip,
                            'monthly_savings': monthly_cost,
                            'released_date': today.isoformat()
                        })
                        results['total_savings_monthly'] += monthly_cost
                        
                    except Exception as e:
                        print(f"❌ ERROR releasing EIP {allocation_id}: {str(e)}")
        
        print(f"\nElastic IPs released: {len(results['eips_released'])}")
        
    except Exception as e:
        print(f"ERROR in EIP cleanup: {str(e)}")
    
    return results

def log_deletions_to_dynamodb(results: Dict):
    """
    Log all deletions to DynamoDB for historical tracking
    """
    try:
        # Log each deleted volume
        for volume in results['volumes_deleted']:
            table.put_item(
                Item={
                    'deletion_id': f"volume-{volume['volume_id']}-{volume['deleted_date']}",
                    'deleted_date': volume['deleted_date'],
                    'resource_type': 'ebs_volume',
                    'resource_id': volume['volume_id'],
                    'size_gb': volume['size_gb'],
                    'volume_type': volume['volume_type'],
                    'monthly_savings': str(volume['monthly_savings']),  # DynamoDB doesn't support float
                    'snapshot_id': volume.get('snapshot_id', 'none')
                }
            )
        
        # Log each deleted snapshot
        for snapshot in results['snapshots_deleted']:
            table.put_item(
                Item={
                    'deletion_id': f"snapshot-{snapshot['snapshot_id']}-{snapshot['deleted_date']}",
                    'deleted_date': snapshot['deleted_date'],
                    'resource_type': 'snapshot',
                    'resource_id': snapshot['snapshot_id'],
                    'size_gb': snapshot['size_gb'],
                    'monthly_savings': str(snapshot['monthly_savings'])
                }
            )
        
        # Log each released EIP
        for eip in results['eips_released']:
            table.put_item(
                Item={
                    'deletion_id': f"eip-{eip['allocation_id']}-{eip['released_date']}",
                    'deleted_date': eip['released_date'],
                    'resource_type': 'elastic_ip',
                    'resource_id': eip['allocation_id'],
                    'public_ip': eip['public_ip'],
                    'monthly_savings': str(eip['monthly_savings'])
                }
            )
        
        print(f"\n✓ Logged {len(results['volumes_deleted']) + len(results['snapshots_deleted']) + len(results['eips_released'])} deletions to DynamoDB")
        
    except Exception as e:
        print(f"\nERROR logging to DynamoDB: {str(e)}")
        # Don't fail the whole function if logging fails


def send_cleanup_report(results: Dict):
    """
    Send cleanup report via SNS
    """
    total_deleted = (len(results['volumes_deleted']) + 
                    len(results['snapshots_deleted']) + 
                    len(results['eips_released']))
    
    total_savings = results['total_savings_monthly']
    
    # Log full details
    print("\n" + "=" * 60)
    print("CLEANUP REPORT")
    print("=" * 60)
    print(f"Total Resources Deleted: {total_deleted}")
    print(f"Monthly Savings: ${total_savings:.2f}")
    print(f"Annual Savings: ${total_savings * 12:.2f}")
    print(f"\nBreakdown:")
    print(f"  Volumes deleted: {len(results['volumes_deleted'])}")
    print(f"  Snapshots deleted: {len(results['snapshots_deleted'])}")
    print(f"  Elastic IPs released: {len(results['eips_released'])}")
    print(f"  Volumes skipped: {len(results['volumes_skipped'])}")
    print("=" * 60)
    
    # SMS message
    if total_deleted == 0:
        message = "AWS Cleanup: No expired resources found. All tagged resources still in grace period."
    else:
        message = f"AWS Cleanup: Deleted {total_deleted} expired resources. "
        message += f"Savings: ${total_savings:.2f}/mo (${total_savings * 12:.2f}/yr). "
        
        if results['volumes_deleted']:
            message += f"{len(results['volumes_deleted'])} volumes. "
        if results['snapshots_deleted']:
            message += f"{len(results['snapshots_deleted'])} snapshots. "
        if results['eips_released']:
            message += f"{len(results['eips_released'])} IPs. "
        
        message += "Safety snapshots created. Check CloudWatch for details."
    
    try:
        response = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"Cleanup: ${total_savings:.2f}/mo saved",
            Message=message
        )
        print(f"\n✓ SMS sent. MessageId: {response['MessageId']}")
    except Exception as e:
        print(f"\nERROR sending SNS: {str(e)}")