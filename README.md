# AWS Cost Optimizer

> Automated FinOps system that detects, tracks, and eliminates AWS cost waste with 7-day grace period and full audit trail

![AWS](https://img.shields.io/badge/AWS-Lambda%20%7C%20EventBridge%20%7C%20DynamoDB-orange)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![Cost](https://img.shields.io/badge/Monthly%20Cost-%243-green)

## 📊 Problem Statement

**Real-world scenario:** A Series B SaaS startup saw their AWS bill grow from $8K/month to $23K/month in 6 months due to:
- Developers forgetting to terminate test instances after use
- 47 unattached EBS volumes accumulating ($340/month wasted)
- Old snapshots never cleaned up (>90 days)
- Idle Elastic IPs costing $3.60 each per month
- No cost monitoring until the monthly bill arrived

**CFO's mandate:** "Get cloud costs under control or we migrate providers."

## 💡 Solution

Automated cost optimization system that:
1. **Scans AWS daily** for 4 types of cost waste
2. **Tags resources** with 7-day grace period for human review
3. **Auto-deletes** after grace period expires
4. **Creates safety snapshots** before deletion (recovery possible)
5. **Tracks savings** in DynamoDB for cumulative reporting
6. **Alerts via SMS** for all actions

## 🏗️ Architecture
```
┌─────────────────────────────────────────────────────────┐
│               AWS COST OPTIMIZER SYSTEM                  │
└─────────────────────────────────────────────────────────┘

EventBridge (6 AM IST)        EventBridge (7 AM IST)
        ↓                              ↓
  CostAnalyzerFunction          ResourceCleanupFunction
        ↓                              ↓
   Scan for waste              Check grace period
        ↓                              ↓
   Tag resources               Create snapshot → Delete
        ↓                              ↓
   Send SMS alert              Log to DynamoDB → SMS alert
                                       ↓
                             CostSavingsQueryFunction
```

### Components

| Component | Purpose | Trigger |
|-----------|---------|---------|
| **CostAnalyzerFunction** | Detects cost waste, tags resources | EventBridge (daily 6 AM IST) |
| **ResourceCleanupFunction** | Deletes expired resources after grace period | EventBridge (daily 7 AM IST) |
| **CostSavingsQueryFunction** | Calculates cumulative savings from DynamoDB | Manual/API Gateway |
| **DynamoDB Table** | Historical record of all deletions | Written by cleanup Lambda |
| **SNS Topic** | SMS alerts for all actions | Published by both Lambdas |

## 🎯 Cost Rules Implemented

| # | Rule | Detection Logic | Action | Avg Savings |
|---|------|----------------|--------|-------------|
| 1 | **Unattached EBS volumes** | `len(volume['Attachments']) == 0` | Tag → Delete after 7 days | $5-15/month per volume |
| 2 | **Stopped EC2 with EBS** | `instance['State'] == 'stopped'` | Tag EBS → Alert owner | $3-8/month per instance |
| 3 | **Old snapshots** | `snapshot_age > 90 days` | Tag → Delete after 7 days | $1-5/month per snapshot |
| 4 | **Idle Elastic IPs** | `'AssociationId' not in address` | Tag → Release after 7 days | $3.60/month per IP |

## 🔑 Key Design Decisions

### 1. Why 7-Day Grace Period?

**Tradeoff:** Safety vs. Speed

❌ **Too short (1-3 days):** Risk deleting resources actively being used  
❌ **Too long (30+ days):** Waste continues, savings delayed  
✅ **7 days:** Balance between safety and cost optimization

**Enterprise pattern:** Varies by environment
- Dev: 3 days (fast iteration)
- Staging: 7 days (review window)
- Production: 30 days (maximum caution)

---

### 2. Why Safety Snapshots Before Deletion?

**Problem:** Accidental deletion is expensive
- Lost data = hours of recovery work
- Lost configuration = infrastructure rebuilt from scratch

**Solution:** Always snapshot EBS volumes before deletion
- Recovery possible for 30 days
- Snapshot cost: ~$0.057/GB/month (cheap insurance)
- Snapshots auto-tagged with original volume ID

**Cost/benefit:**
- 10 GB volume = $1.14/month
- 10 GB snapshot = $0.57/month
- **Net savings still 50%** even with snapshot retained

---

### 3. Why DynamoDB for Tracking?

**Alternatives considered:**
- **S3 with JSON files:** Harder to query, no aggregation
- **CloudWatch Logs only:** Not structured, expensive to analyze
- **RDS:** Overkill, requires management, costs more

**DynamoDB advantages:**
- Serverless (no management)
- Pay-per-request (on-demand mode)
- Free Tier covers typical usage
- Easy to query for cumulative metrics
- Integrates seamlessly with Lambda

**Cost:** $0.00/month (under Free Tier limits)

---

### 4. Why SMS vs. Email?

**Email problems:**
- Gmail blocks AWS SMTP by default
- Often goes to spam folder
- Not checked frequently enough

**SMS advantages:**
- Immediate delivery (30-60 seconds)
- High open rate (98% vs. 20% for email)
- Critical for cost alerts

**Tradeoff:** SMS costs $0.033 each (~$3/month for 90 alerts)

---

### 5. Why Tag-Based vs. Immediate Deletion?

**Immediate deletion risks:**
- No human review opportunity
- Can't distinguish test vs. production resources
- No recovery window if mistake made

**Tag-based advantages:**
- Visible in AWS Console (filter by tag to review)
- Team can remove tag to prevent deletion
- Audit trail of what was marked
- Grace period for second thoughts

**Pattern:** `CostOptimization=DeleteAfter-YYYY-MM-DD`

## 🔒 Security & Compliance

### IAM Least Privilege

**CostAnalyzerLambdaRole:**
```json
{
  "Allows": [
    "ec2:Describe*",           // Read-only access to resources
    "ec2:CreateTags",          // Tag resources for deletion
    "sns:Publish"              // Send alerts
  ],
  "Denies": [
    "ec2:Delete*",             // Cannot delete anything
    "ec2:Terminate*",          // Cannot terminate instances
    "iam:*"                    // Cannot modify permissions
  ]
}
```

**ResourceCleanupLambdaRole:**
```json
{
  "Allows": [
    "ec2:DescribeVolumes",     // Read resources
    "ec2:DeleteVolume",        // Delete after verification
    "ec2:CreateSnapshot",      // Safety snapshots
    "dynamodb:PutItem",        // Log deletions
    "sns:Publish"              // Send alerts
  ],
  "Conditions": [
    "Only resources tagged 'CostOptimization'"  // Limited blast radius
  ]
}
```

### Audit Trail

Every action logged in **3 places**:
1. **CloudWatch Logs:** Immutable, detailed execution logs
2. **DynamoDB:** Permanent record of deletions with metadata
3. **SNS Alerts:** Timestamped SMS notifications

**Example audit record:**
```json
{
  "deletion_id": "volume-vol-abc123-2025-02-02",
  "deleted_date": "2025-02-02",
  "resource_type": "ebs_volume",
  "resource_id": "vol-abc123",
  "size_gb": 10,
  "monthly_savings": "0.91",
  "snapshot_id": "snap-def456"
}
```

## 📈 Results & Impact

### Cost Savings (Projected)

| Team Size | Monthly Savings | Annual Savings | System ROI |
|-----------|----------------|----------------|------------|
| 10 developers | $500-1,000 | $6,000-12,000 | 200:1 |
| 50 developers | $2,000-5,000 | $24,000-60,000 | 1000:1 |
| 200+ developers | $10,000-20,000 | $120,000-240,000 | 4000:1 |

### System Operating Cost

| Component | Monthly Cost |
|-----------|--------------|
| Lambda invocations | $0.00 (Free Tier) |
| DynamoDB on-demand | $0.00 (Free Tier) |
| CloudWatch Logs | $0.50 |
| SNS SMS (~90 messages) | $3.00 |
| **TOTAL** | **$3.50/month** |

### Query Example (Cumulative Savings)
```bash
# Invoke query Lambda
aws lambda invoke --function-name CostSavingsQueryFunction output.json

# Output:
{
  "total_resources_deleted": 47,
  "total_monthly_savings": 847.23,
  "total_annual_savings": 10166.76,
  "resource_breakdown": {
    "ebs_volume": 32,
    "snapshot": 12,
    "elastic_ip": 3
  },
  "days_operating": 45,
  "average_savings_per_day": 28.24
}
```

## 🧪 Testing & Validation

### Test Scenarios Executed

| Test | Expected Result | Actual Result | Status |
|------|----------------|---------------|--------|
| Create unattached volume | Tagged within 24 hours | Tagged in 1 day | ✅ Pass |
| Grace period expires | Volume deleted + snapshot created | Worked as expected | ✅ Pass |
| Volume reattached during grace | Deletion skipped | Correctly skipped | ✅ Pass |
| Recovery from snapshot | Volume restored successfully | Full recovery verified | ✅ Pass |
| DynamoDB logging | Deletion logged with metadata | All fields populated | ✅ Pass |
| SMS alerts | Received within 60 seconds | Avg 30 seconds | ✅ Pass |

### Edge Cases Handled

1. **Volume reattached during grace period**
   - Detection: Check `len(Attachments) == 0` before deletion
   - Action: Skip deletion, remove tag, log event

2. **Snapshot creation fails**
   - Detection: `ec2.create_snapshot()` raises exception
   - Action: Abort deletion, log error, alert operator

3. **DynamoDB write fails**
   - Detection: `table.put_item()` raises exception
   - Action: Continue deletion (logging is non-critical), log to CloudWatch

4. **Lambda timeout**
   - Detection: Execution > 60 seconds
   - Action: Increased timeout, optimized boto3 calls

## 🚀 Deployment

### Prerequisites
- AWS account with IAM admin access
- AWS CLI configured
- Python 3.12

### Quick Start

1. **Clone repository**
```bash
git clone https://github.com/yourusername/aws-cost-optimizer
cd aws-cost-optimizer
```

2. **Deploy infrastructure**
```bash
# Create DynamoDB table
aws dynamodb create-table \
  --table-name CostOptimizationLog \
  --attribute-definitions AttributeName=deletion_id,AttributeType=S AttributeName=deleted_date,AttributeType=S \
  --key-schema AttributeName=deletion_id,KeyType=HASH AttributeName=deleted_date,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST

# Create SNS topic
aws sns create-topic --name cost-alerts-topic

# Subscribe your phone to SNS (replace with your number)
aws sns subscribe \
  --topic-arn arn:aws:sns:ap-south-1:ACCOUNT_ID:cost-alerts-topic \
  --protocol sms \
  --notification-endpoint +919876543210
```

3. **Deploy Lambda functions**
```bash
# Create IAM roles (see deployment/iam-roles.json)
aws iam create-role --role-name CostAnalyzerLambdaRole --assume-role-policy-document file://trust-policy.json
aws iam put-role-policy --role-name CostAnalyzerLambdaRole --policy-name CostAnalyzerPolicy --policy-document file://analyzer-policy.json

# Deploy functions
cd lambda
zip -r cost-analyzer.zip cost_analyzer.py
aws lambda create-function \
  --function-name CostAnalyzerFunction \
  --runtime python3.12 \
  --role arn:aws:iam::ACCOUNT_ID:role/CostAnalyzerLambdaRole \
  --handler cost_analyzer.lambda_handler \
  --zip-file fileb://cost-analyzer.zip \
  --timeout 30
```

4. **Create EventBridge schedules**
```bash
# Daily 6 AM IST (0:30 UTC) - Cost Analyzer
aws scheduler create-schedule \
  --name DailyCostAnalysis \
  --schedule-expression "cron(30 0 * * ? *)" \
  --target '{"Arn":"arn:aws:lambda:ap-south-1:ACCOUNT_ID:function:CostAnalyzerFunction","RoleArn":"arn:aws:iam::ACCOUNT_ID:role/EventBridgeRole"}'

# Daily 7 AM IST (1:30 UTC) - Cleanup
aws scheduler create-schedule \
  --name DailyResourceCleanup \
  --schedule-expression "cron(30 1 * * ? *)" \
  --target '{"Arn":"arn:aws:lambda:ap-south-1:ACCOUNT_ID:function:ResourceCleanupFunction","RoleArn":"arn:aws:iam::ACCOUNT_ID:role/EventBridgeRole"}'
```

5. **Test the system**
```bash
# Enable DRY_RUN mode first (in Lambda code)
# Create test volume
aws ec2 create-volume --size 1 --availability-zone ap-south-1a --volume-type gp3

# Manually invoke analyzer
aws lambda invoke --function-name CostAnalyzerFunction output.json

# Check if volume was tagged
aws ec2 describe-volumes --filters "Name=tag:CostOptimization,Values=DeleteAfter-*"
```

## 🐛 Troubleshooting

### Common Issues

**1. Lambda "Access Denied" errors**
```
Error: User is not authorized to perform: ec2:DescribeVolumes
```
**Fix:** Verify IAM role attached to Lambda has correct policies

**2. SNS messages not received**
```
Error: AuthorizationError when calling Publish operation
```
**Fix:** Check SNS topic ARN in Lambda code, verify SMS subscription confirmed

**3. EventBridge not triggering Lambda**
```
Lambda not executing at scheduled time
```
**Fix:** Add Lambda permission for EventBridge
```bash
aws lambda add-permission \
  --function-name CostAnalyzerFunction \
  --statement-id AllowEventBridge \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com
```

**4. DynamoDB write failures**
```
Error: User is not authorized to perform: dynamodb:PutItem
```
**Fix:** Add DynamoDB permissions to Lambda role

## 📚 What I Learned

### Technical Skills
- **Event-driven architecture:** EventBridge + Lambda patterns
- **Serverless design:** Building with managed services
- **IAM security:** Implementing least-privilege access
- **Cost optimization:** Identifying and eliminating cloud waste
- **Error handling:** Graceful degradation and retry logic

### Cloud Engineering Principles
1. **Grace periods matter:** Safety nets prevent costly mistakes
2. **Audit everything:** Logs are critical for debugging and compliance
3. **Test failure modes:** What happens when things break?
4. **Tag-based governance:** Metadata enables powerful automation
5. **Cost-conscious design:** Build systems that pay for themselves

### Production Debugging
- SMS character limits broke initial alerts (learned to be concise)
- IAM permissions are easy to misconfigure (learned systematic verification)
- EventBridge permissions aren't always auto-created (learned manual permission grants)
- DynamoDB on-demand mode is perfect for low-traffic workloads

## 📄 License

MIT License - See LICENSE file

## 👤 Author

**Abdul Ahad**
- Portfolio: [your-portfolio.com]
- LinkedIn: [[linkedin.com/in/yourprofile](https://www.linkedin.com/in/abdul-ahad-97480b297/)]
- GitHub: [@Abd2301]

## 🙏 Acknowledgments

- Inspired by real-world FinOps challenges at fast-growing startups
- Built to demonstrate cloud engineering and automation skills
- Designed for production deployment at enterprise scale
