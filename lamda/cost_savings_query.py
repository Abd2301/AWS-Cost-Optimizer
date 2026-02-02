import boto3
import json
from decimal import Decimal
from datetime import datetime, timedelta

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('CostOptimizationLog')

def lambda_handler(event, context):
    """
    Query DynamoDB for cumulative cost savings
    """
    try:
        # Scan all items (small table, this is fine)
        response = table.scan()
        items = response['Items']
        
        # Calculate totals
        total_monthly_savings = 0
        resource_counts = {
            'ebs_volume': 0,
            'snapshot': 0,
            'elastic_ip': 0
        }
        
        for item in items:
            savings = float(item.get('monthly_savings', 0))
            total_monthly_savings += savings
            
            resource_type = item.get('resource_type', 'unknown')
            if resource_type in resource_counts:
                resource_counts[resource_type] += 1
        
        # Calculate date range
        if items:
            dates = [item['deleted_date'] for item in items]
            first_deletion = min(dates)
            last_deletion = max(dates)
            
            # Calculate days of operation
            first_date = datetime.strptime(first_deletion, '%Y-%m-%d')
            last_date = datetime.strptime(last_deletion, '%Y-%m-%d')
            days_operating = (last_date - first_date).days + 1
        else:
            first_deletion = 'N/A'
            last_deletion = 'N/A'
            days_operating = 0
        
        result = {
            'total_resources_deleted': len(items),
            'total_monthly_savings': round(total_monthly_savings, 2),
            'total_annual_savings': round(total_monthly_savings * 12, 2),
            'resource_breakdown': resource_counts,
            'first_deletion_date': first_deletion,
            'last_deletion_date': last_deletion,
            'days_operating': days_operating,
            'average_savings_per_day': round(total_monthly_savings / 30, 2) if total_monthly_savings > 0 else 0
        }
        
        print(json.dumps(result, indent=2))
        
        return {
            'statusCode': 200,
            'body': json.dumps(result, indent=2)
        }
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }