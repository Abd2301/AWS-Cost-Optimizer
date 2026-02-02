import boto3
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# Initialize AWS clients
ec2 = boto3.client('ec2')
sns = boto3.client('sns')

# Configuration
SNS_TOPIC_ARN = 'arn:aws:sns:ap-south-1:574337396853:cost-alerts-topic'  # UPDATE THIS
TAG_KEY = 'CostOptimization'
TAG_VALUE_PREFIX = 'DeleteAfter-'
GRACE_PERIOD_DAYS = 7
SNAPSHOT_AGE_THRESHOLD_DAYS = 90

def lambda_handler(event, context):
    """
    Main Lambda function to analyze AWS costs and identify waste
    """
    print("=" * 60)
    print("STARTING COST ANALYSIS SCAN")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    findings = {
        'unattached_volumes': [],
        'stopped_instances_with_volumes': [],
        'old_snapshots': [],
        'idle_elastic_ips': [],
        'total_waste_monthly': 0.0
    }
    
    # Run all cost checks
    findings['unattached_volumes'] = find_unattached_volumes()
    findings['stopped_instances_with_volumes'] = find_stopped_instances_with_volumes()
    findings['old_snapshots'] = find_old_snapshots()
    findings['idle_elastic_ips'] = find_idle_elastic_ips()
    
    # Calculate total waste
    for volume in findings['unattached_volumes']:
        findings['total_waste_monthly'] += volume['monthly_cost']
    
    for instance in findings['stopped_instances_with_volumes']:
        findings['total_waste_monthly'] += instance['monthly_cost']
    
    for snapshot in findings['old_snapshots']:
        findings['total_waste_monthly'] += snapshot['monthly_cost']
    
    for eip in findings['idle_elastic_ips']:
        findings['total_waste_monthly'] += eip['monthly_cost']
    
    # Tag resources for deletion
    tag_resources_for_deletion(findings)
    
    # Send report
    send_cost_report(findings)
    
    print("=" * 60)
    print("COST ANALYSIS COMPLETE")
    print("=" * 60)
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Cost analysis complete',
            'total_waste': findings['total_waste_monthly'],
            'findings_count': sum([
                len(findings['unattached_volumes']),
                len(findings['stopped_instances_with_volumes']),
                len(findings['old_snapshots']),
                len(findings['idle_elastic_ips'])
            ])
        }, default=str)
    }

def find_unattached_volumes():
    """
    Find EBS volumes not attached to any EC2 instance
    """
    unattached = []
    
    try:
        response = ec2.describe_volumes()
        
        for volume in response['Volumes']:
            if len(volume['Attachments']) == 0:
                volume_type = volume['VolumeType']
                size_gb = volume['Size']
                
                # Pricing for ap-south-1 (Mumbai)
                price_per_gb = {
                    'gp2': 0.114,
                    'gp3': 0.091,
                    'io1': 0.143,
                    'io2': 0.143,
                    'sc1': 0.029,
                    'st1': 0.051,
                    'standard': 0.057
                }
                
                monthly_cost = size_gb * price_per_gb.get(volume_type, 0.10)
                
                unattached.append({
                    'resource_type': 'ebs_volume',
                    'resource_id': volume['VolumeId'],
                    'volume_id': volume['VolumeId'],
                    'size_gb': size_gb,
                    'volume_type': volume_type,
                    'monthly_cost': round(monthly_cost, 2),
                    'create_time': volume['CreateTime'].isoformat(),
                    'availability_zone': volume['AvailabilityZone'],
                    'tags': volume.get('Tags', [])
                })
                
                print(f"✓ Unattached volume: {volume['VolumeId']} ({size_gb}GB {volume_type}) - ${monthly_cost:.2f}/month")
        
        print(f"\nTotal unattached volumes: {len(unattached)}")
        return unattached
        
    except Exception as e:
        print(f"ERROR finding unattached volumes: {str(e)}")
        return []

def find_stopped_instances_with_volumes():
    """
    Find stopped EC2 instances that still have EBS volumes attached
    These volumes are costing money even though instance is stopped
    """
    stopped_with_volumes = []
    
    try:
        response = ec2.describe_instances(
            Filters=[
                {'Name': 'instance-state-name', 'Values': ['stopped']}
            ]
        )
        
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                # Calculate EBS cost for this stopped instance
                total_ebs_cost = 0
                volume_details = []
                
                for bdm in instance.get('BlockDeviceMappings', []):
                    if 'Ebs' in bdm:
                        volume_id = bdm['Ebs']['VolumeId']
                        
                        # Get volume details
                        vol_response = ec2.describe_volumes(VolumeIds=[volume_id])
                        if vol_response['Volumes']:
                            vol = vol_response['Volumes'][0]
                            size_gb = vol['Size']
                            volume_type = vol['VolumeType']
                            
                            price_per_gb = {
                                'gp2': 0.114,
                                'gp3': 0.091,
                                'io1': 0.143,
                                'io2': 0.143,
                                'sc1': 0.029,
                                'st1': 0.051,
                                'standard': 0.057
                            }
                            
                            volume_cost = size_gb * price_per_gb.get(volume_type, 0.10)
                            total_ebs_cost += volume_cost
                            
                            volume_details.append({
                                'volume_id': volume_id,
                                'size_gb': size_gb,
                                'type': volume_type
                            })
                
                if total_ebs_cost > 0:
                    # Get instance name from tags
                    instance_name = 'unnamed'
                    for tag in instance.get('Tags', []):
                        if tag['Key'] == 'Name':
                            instance_name = tag['Value']
                            break
                    
                    # Calculate stopped duration
                    state_transition_time = instance.get('StateTransitionReason', '')
                    
                    stopped_with_volumes.append({
                        'resource_type': 'stopped_instance',
                        'resource_id': instance['InstanceId'],
                        'instance_id': instance['InstanceId'],
                        'instance_name': instance_name,
                        'instance_type': instance['InstanceType'],
                        'monthly_cost': round(total_ebs_cost, 2),
                        'volumes': volume_details,
                        'state_transition': state_transition_time,
                        'tags': instance.get('Tags', [])
                    })
                    
                    print(f"✓ Stopped instance: {instance['InstanceId']} ({instance_name}) with {len(volume_details)} volumes - ${total_ebs_cost:.2f}/month in EBS costs")
        
        print(f"\nTotal stopped instances with volumes: {len(stopped_with_volumes)}")
        return stopped_with_volumes
        
    except Exception as e:
        print(f"ERROR finding stopped instances: {str(e)}")
        return []

def find_old_snapshots():
    """
    Find snapshots older than 90 days
    Old snapshots are often forgotten and accumulate costs
    """
    old_snapshots = []
    
    try:
        # Get snapshots owned by this account
        response = ec2.describe_snapshots(OwnerIds=['self'])
        
        threshold_date = datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_AGE_THRESHOLD_DAYS)
        
        for snapshot in response['Snapshots']:
            snapshot_date = snapshot['StartTime']
            
            if snapshot_date < threshold_date:
                # Snapshot pricing: $0.057 per GB/month in ap-south-1
                size_gb = snapshot['VolumeSize']
                monthly_cost = size_gb * 0.057
                
                age_days = (datetime.now(timezone.utc) - snapshot_date).days
                
                old_snapshots.append({
                    'resource_type': 'snapshot',
                    'resource_id': snapshot['SnapshotId'],
                    'snapshot_id': snapshot['SnapshotId'],
                    'size_gb': size_gb,
                    'monthly_cost': round(monthly_cost, 2),
                    'age_days': age_days,
                    'start_time': snapshot_date.isoformat(),
                    'description': snapshot.get('Description', 'No description'),
                    'tags': snapshot.get('Tags', [])
                })
                
                print(f"✓ Old snapshot: {snapshot['SnapshotId']} ({age_days} days old, {size_gb}GB) - ${monthly_cost:.2f}/month")
        
        print(f"\nTotal old snapshots (>{SNAPSHOT_AGE_THRESHOLD_DAYS} days): {len(old_snapshots)}")
        return old_snapshots
        
    except Exception as e:
        print(f"ERROR finding old snapshots: {str(e)}")
        return []

def find_idle_elastic_ips():
    """
    Find Elastic IPs not associated with any instance
    Idle EIPs cost money ($0.005/hour = ~$3.60/month in ap-south-1)
    """
    idle_eips = []
    
    try:
        response = ec2.describe_addresses()
        
        for address in response['Addresses']:
            # If AssociationId is missing, EIP is not attached
            if 'AssociationId' not in address:
                # Idle EIP cost: $0.005/hour in ap-south-1
                monthly_cost = 0.005 * 24 * 30  # ~$3.60/month
                
                idle_eips.append({
                    'resource_type': 'elastic_ip',
                    'resource_id': address['AllocationId'],
                    'allocation_id': address['AllocationId'],
                    'public_ip': address['PublicIp'],
                    'monthly_cost': round(monthly_cost, 2),
                    'tags': address.get('Tags', [])
                })
                
                print(f"✓ Idle Elastic IP: {address['PublicIp']} ({address['AllocationId']}) - ${monthly_cost:.2f}/month")
        
        print(f"\nTotal idle Elastic IPs: {len(idle_eips)}")
        return idle_eips
        
    except Exception as e:
        print(f"ERROR finding idle Elastic IPs: {str(e)}")
        return []

def tag_resources_for_deletion(findings):
    """
    Tag all identified resources for deletion after grace period
    """
    delete_after_date = datetime.now() + timedelta(days=GRACE_PERIOD_DAYS)
    tag_value = f"{TAG_VALUE_PREFIX}{delete_after_date.strftime('%Y-%m-%d')}"
    
    resources_to_tag = []
    
    # Collect all resource IDs
    for volume in findings['unattached_volumes']:
        resources_to_tag.append(volume['volume_id'])
    
    for snapshot in findings['old_snapshots']:
        resources_to_tag.append(snapshot['snapshot_id'])
    
    # Tag in batches (EC2 API limit: 1000 resources per call)
    if resources_to_tag:
        try:
            ec2.create_tags(
                Resources=resources_to_tag,
                Tags=[
                    {'Key': TAG_KEY, 'Value': tag_value},
                    {'Key': 'AutomatedBy', 'Value': 'CostAnalyzer'},
                    {'Key': 'FoundDate', 'Value': datetime.now().strftime('%Y-%m-%d')}
                ]
            )
            print(f"\n✓ Tagged {len(resources_to_tag)} resources for deletion on {delete_after_date.strftime('%Y-%m-%d')}")
        except Exception as e:
            print(f"ERROR tagging resources: {str(e)}")
    
    # Tag Elastic IPs separately (different API)
    for eip in findings['idle_elastic_ips']:
        try:
            ec2.create_tags(
                Resources=[eip['allocation_id']],
                Tags=[
                    {'Key': TAG_KEY, 'Value': tag_value},
                    {'Key': 'AutomatedBy', 'Value': 'CostAnalyzer'},
                    {'Key': 'FoundDate', 'Value': datetime.now().strftime('%Y-%m-%d')}
                ]
            )
        except Exception as e:
            print(f"ERROR tagging EIP {eip['allocation_id']}: {str(e)}")

def send_cost_report(findings):
    """
    Send cost analysis report via SNS (SMS-friendly format)
    """
    unattached_count = len(findings['unattached_volumes'])
    stopped_count = len(findings['stopped_instances_with_volumes'])
    snapshot_count = len(findings['old_snapshots'])
    eip_count = len(findings['idle_elastic_ips'])
    
    total_issues = unattached_count + stopped_count + snapshot_count + eip_count
    total_waste = findings['total_waste_monthly']
    
    # Log full details to CloudWatch
    print("\n" + "=" * 60)
    print("COST ANALYSIS REPORT")
    print("=" * 60)
    print(f"Total Monthly Waste: ${total_waste:.2f}")
    print(f"Annual Waste: ${total_waste * 12:.2f}")
    print(f"\nBreakdown:")
    print(f"  Unattached EBS Volumes: {unattached_count}")
    print(f"  Stopped Instances (EBS cost): {stopped_count}")
    print(f"  Old Snapshots (>90 days): {snapshot_count}")
    print(f"  Idle Elastic IPs: {eip_count}")
    print("=" * 60)
    
    # SMS-friendly message
    if total_issues == 0:
        message = "AWS Cost Alert: No waste found. All resources optimized."
    else:
        message = f"AWS Cost Alert: {total_issues} issues found. "
        message += f"Savings: ${total_waste:.2f}/mo (${total_waste * 12:.2f}/yr). "
        
        if unattached_count > 0:
            message += f"{unattached_count} unattached volumes. "
        if stopped_count > 0:
            message += f"{stopped_count} stopped instances. "
        if snapshot_count > 0:
            message += f"{snapshot_count} old snapshots. "
        if eip_count > 0:
            message += f"{eip_count} idle IPs. "
        
        message += "Tagged for 7-day review."
    
    try:
        response = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"Cost Alert: ${total_waste:.2f}/mo",
            Message=message
        )
        print(f"\n✓ SMS sent. MessageId: {response['MessageId']}")
        print(f"Message length: {len(message)} chars")
        
    except Exception as e:
        print(f"\nERROR sending SNS: {str(e)}")