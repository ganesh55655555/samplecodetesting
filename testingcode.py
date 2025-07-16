import boto3
import pandas as pd
from datetime import datetime
from pandas import ExcelWriter

# ---------- Common Setup ----------
session = boto3.Session(region_name='ap-south-1')
ec2_resource = session.resource('ec2')
ec2_client = session.client('ec2')
s3 = session.client('s3')
sts = session.client('sts')
account_id = sts.get_caller_identity()['Account']
timestamp = datetime.now().strftime('%Y-%m-%d')

# ---------- Helper Maps ----------
instance_name_map = {}
for reservation in ec2_client.describe_instances()['Reservations']:
    for inst in reservation['Instances']:
        name = next((tag['Value'] for tag in inst.get('Tags', []) if tag['Key'] == 'Name'), "")
        instance_name_map[inst['InstanceId']] = name

vpcs = ec2_client.describe_vpcs()['Vpcs']
route_tables = ec2_client.describe_route_tables()['RouteTables']
acls = ec2_client.describe_network_acls()['NetworkAcls']
subnets = ec2_client.describe_subnets()['Subnets']
vpc_name_map = {v['VpcId']: next((t['Value'] for t in v.get('Tags', []) if t['Key'] == 'Name'), '–') for v in vpcs}

def get_main_route_table(vpc_id):
    for rt in route_tables:
        for assoc in rt.get('Associations', []):
            if assoc.get('Main') and rt.get('VpcId') == vpc_id:
                return rt['RouteTableId']
    return '–'

def get_main_acl(vpc_id):
    for acl in acls:
        if acl['VpcId'] == vpc_id and acl.get('IsDefault', False):
            return acl['NetworkAclId']
    return '–'

def get_route_table_for_subnet(subnet_id):
    for rt in route_tables:
        for assoc in rt.get('Associations', []):
            if assoc.get('SubnetId') == subnet_id:
                return f"{rt['RouteTableId']} | {assoc.get('RouteTableAssociationId', '')}"
    return "–"

def get_acl_for_subnet(subnet_id):
    for acl in acls:
        for assoc in acl['Associations']:
            if assoc['SubnetId'] == subnet_id:
                return acl['NetworkAclId']
    return "–"

# ---------- EC2 Instances ----------
instances_data = []
for instance in ec2_resource.instances.all():
    state = instance.state['Name']
    name = next((tag['Value'] for tag in instance.tags or [] if tag['Key'] == 'Name'), '')
    sg_names = ', '.join([sg['GroupName'] for sg in instance.security_groups])
    public_ip = instance.public_ip_address or '–'
    launch_time = instance.launch_time.astimezone().strftime("%Y/%m/%d %H:%M GMT%z")
    instances_data.append({
        'Name': name,
        'Instance ID': instance.id,
        'Instance state': state.capitalize(),
        'Instance type': instance.instance_type,
        'Status check': "3/3 checks passed",  # Placeholder
        'Alarm status': "View alarms",
        'Availability Zone': instance.placement['AvailabilityZone'],
        'Public IPv4 address': public_ip,
        'Elastic IP': public_ip,
        'IPv6 IPs': '–',
        'Monitoring': 'disabled' if instance.monitoring['State'] == 'disabled' else 'enabled',
        'Security group name': sg_names,
        'Key name': instance.key_name or '–',
        'Launch time': launch_time,
        'Platform details': 'Linux/UNIX' if not instance.platform else instance.platform
    })

# ---------- EBS Volumes ----------
volumes_data = []
for vol in ec2_client.describe_volumes()['Volumes']:
    attachments = vol.get('Attachments', [])
    if attachments:
        instance_id = attachments[0]['InstanceId']
        device = attachments[0]['Device']
        name_tag = instance_name_map.get(instance_id, '')
        attach_str = f"{instance_id} ({name_tag}): {device} (attached)"
    else:
        attach_str = "—"
        name_tag = ''
    volumes_data.append({
        'Name': name_tag + "_root" if name_tag else "—",
        'Volume ID': vol['VolumeId'],
        'Type': vol['VolumeType'],
        'Size': f"{vol['Size']}GiB",
        'IOPS': vol.get('Iops', '—'),
        'Throughput': vol.get('Throughput', '—'),
        'Snapshot ID': vol.get('SnapshotId', '—'),
        'Created': vol['CreateTime'].astimezone().strftime("%Y/%m/%d %H:%M GMT%z"),
        'Availability Zone': vol['AvailabilityZone'],
        'Volume state': vol['State'].capitalize(),
        'Alarm status': "No alarms",
        'Attached resources': attach_str,
        'Encryption': "Encrypted" if vol['Encrypted'] else "Not encrypted",
        'KMS key ID': vol.get('KmsKeyId', '–') if vol['Encrypted'] else '–',
        'KMS key alias': '-' if not vol['Encrypted'] else 'Lookup manually',
        'Fast snapshot restored': "No",
        'Multi-Attach enabled': "Yes" if vol.get('MultiAttachEnabled') else "No"
    })

# ---------- Security Groups ----------
sg_data = []
for sg in ec2_client.describe_security_groups()['SecurityGroups']:
    name = next((tag['Value'] for tag in sg.get('Tags', []) if tag['Key'] == 'Name'), '–')
    sg_data.append({
        "Name": name,
        "Security group ID": sg['GroupId'],
        "Security group name": sg['GroupName'],
        "VPC ID": sg.get('VpcId', '—'),
        "Description": sg.get('Description', '—'),
        "Owner": sg['OwnerId'],
        "Inbound rules count": f"{len(sg['IpPermissions'])} Permission(s)",
        "Outbound rules count": f"{len(sg['IpPermissionsEgress'])} Permission(s)"
    })

# ---------- Elastic IPs ----------
eip_data = []
for eip in ec2_client.describe_addresses()['Addresses']:
    eip_data.append({
        "Name": next((tag['Value'] for tag in eip.get('Tags', []) if tag['Key'] == 'Name'), '–'),
        "Allocated IPv4 address": eip.get('PublicIp', '–'),
        "Type": 'Public IP',
        "Allocation ID": eip.get('AllocationId', '–'),
        "Reverse DNS record": '–',
        "Associated instance ID": eip.get('InstanceId', '–'),
        "Private IP address": eip.get('PrivateIpAddress', '–'),
        "Association ID": eip.get('AssociationId', '–'),
        "Network interface owner account ID": eip.get('NetworkInterfaceOwnerId', '–'),
        "Network border group": eip.get('NetworkBorderGroup', '–')
    })

# ---------- S3 Buckets ----------
s3_data = []
for bucket in s3.list_buckets()['Buckets']:
    name = bucket['Name']
    created = bucket['CreationDate'].astimezone().strftime('%Y-%m-%d %H:%M:%S')
    try:
        region = s3.get_bucket_location(Bucket=name).get('LocationConstraint') or 'us-east-1'
    except Exception:
        region = 'Unknown'
    region_label = f"Asia Pacific (Mumbai) {region}" if region == 'ap-south-1' else region
    s3_data.append({
        "Name": name,
        "AWS Region": region_label,
        "IAM Access Analyzer": f"View analyzer for {region}",
        "Creation date": created
    })

# ---------- VPCs ----------
vpc_data = []
for vpc in vpcs:
    vpc_id = vpc['VpcId']
    name = next((tag['Value'] for tag in vpc.get('Tags', []) if tag['Key'] == 'Name'), '–')
    vpc_data.append({
        "Name": name,
        "VPC ID": vpc_id,
        "State": vpc['State'],
        "IPv4 CIDR": vpc['CidrBlock'],
        "IPv6 CIDR": vpc.get('Ipv6CidrBlockAssociationSet', [{'Ipv6CidrBlock': '–'}])[0]['Ipv6CidrBlock'],
        "DHCP option set": vpc['DhcpOptionsId'],
        "Main route table": get_main_route_table(vpc_id),
        "Main network ACL": get_main_acl(vpc_id),
        "Tenancy": vpc['InstanceTenancy'].capitalize(),
        "Default VPC": "Yes" if vpc.get('IsDefault') else "No",
        "Owner ID": account_id
    })

# ---------- Subnets ----------
subnet_data = []
for subnet in subnets:
    subnet_id = subnet['SubnetId']
    vpc_id = subnet['VpcId']
    name = next((tag['Value'] for tag in subnet.get('Tags', []) if tag['Key'] == 'Name'), '–')
    subnet_data.append({
        "Name": name,
        "Subnet ID": subnet_id,
        "State": subnet['State'],
        "VPC": f"{vpc_id} | {vpc_name_map.get(vpc_id, '–')}",
        "IPv4 CIDR": subnet['CidrBlock'],
        "IPv6 CIDR": "–",
        "Available IPv4 addresses": subnet['AvailableIpAddressCount'],
        "Availability Zone": subnet['AvailabilityZone'],
        "Route table": get_route_table_for_subnet(subnet_id),
        "Network ACL": get_acl_for_subnet(subnet_id),
        "Default subnet": "Yes" if subnet.get('DefaultForAz') else "No",
        "Auto-assign public IPv4": "Yes" if subnet.get('MapPublicIpOnLaunch') else "No",
        "Owner ID": account_id
    })

# ---------- Internet Gateways ----------
igw_data = []
for igw in ec2_client.describe_internet_gateways()['InternetGateways']:
    igw_id = igw['InternetGatewayId']
    vpc_id = igw['Attachments'][0]['VpcId'] if igw['Attachments'] else '–'
    state = igw['Attachments'][0].get('State', '–') if igw['Attachments'] else '–'
    name = next((tag['Value'] for tag in igw.get('Tags', []) if tag['Key'] == 'Name'), '–')
    igw_data.append({
        "Name": name,
        "Internet Gateway ID": igw_id,
        "Attached VPC": vpc_id,
        "Attachment State": state,
        "Owner ID": account_id
    })

# ---------- Route Tables ----------
rt_data = []
for rt in route_tables:
    vpc_id = rt['VpcId']
    route_table_id = rt['RouteTableId']
    name = next((tag['Value'] for tag in rt.get('Tags', []) if tag['Key'] == 'Name'), '–')
    assoc_ids = [assoc.get('SubnetId', 'Main') for assoc in rt.get('Associations', [])]
    route_count = len(rt.get('Routes', []))
    rt_data.append({
        "Name": name,
        "Route Table ID": route_table_id,
        "VPC ID": vpc_id,
        "Associations": ', '.join(assoc_ids),
        "Route count": route_count,
        "Owner ID": account_id
    })

# ---------- Network ACLs ----------
nacl_data = []
for acl in acls:
    nacl_id = acl['NetworkAclId']
    vpc_id = acl['VpcId']
    is_default = acl.get('IsDefault', False)
    entry_count = len(acl.get('Entries', []))
    subnet_ids = ', '.join([a['SubnetId'] for a in acl.get('Associations', [])])
    nacl_data.append({
        "Network ACL ID": nacl_id,
        "VPC ID": vpc_id,
        "Default ACL": "Yes" if is_default else "No",
        "Entry count": entry_count,
        "Associated Subnets": subnet_ids,
        "Owner ID": account_id
    })

# ---------- Save to Excel ----------
excel_path = f"aws_inventory_{timestamp}.xlsx"
with ExcelWriter(excel_path, engine='xlsxwriter') as writer:
    pd.DataFrame(instances_data).to_excel(writer, sheet_name='EC2 Instances', index=False)
    pd.DataFrame(volumes_data).to_excel(writer, sheet_name='EBS Volumes', index=False)
    pd.DataFrame(sg_data).to_excel(writer, sheet_name='Security Groups', index=False)
    pd.DataFrame(eip_data).to_excel(writer, sheet_name='Elastic IPs', index=False)
    pd.DataFrame(s3_data).to_excel(writer, sheet_name='S3 Buckets', index=False)
    pd.DataFrame(vpc_data).to_excel(writer, sheet_name='VPCs', index=False)
    pd.DataFrame(subnet_data).to_excel(writer, sheet_name='Subnets', index=False)
    pd.DataFrame(igw_data).to_excel(writer, sheet_name='Internet Gateways', index=False)
    pd.DataFrame(rt_data).to_excel(writer, sheet_name='Route Tables', index=False)
    pd.DataFrame(nacl_data).to_excel(writer, sheet_name='Network ACLs', index=False)

print(f"✅ AWS inventory saved to {excel_path}")
