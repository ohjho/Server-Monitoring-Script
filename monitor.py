import argparse, socket, time, json, datetime, platform, psutil, requests, pprint, uuid
from retrying import retry
import yaml

# parse args
parser = argparse.ArgumentParser(description='Monitoring script to send system info to a tracking server')
parser.add_argument('-d', '--dest', default= None, help='API Endpoint for Monitoring Data (Defaults to None)')
parser.add_argument('-i', '--interval', default=5, type=int, help='Interval between checks (Seconds. Defaults to 5 seconds)')
parser.add_argument('-a', '--attempts', default=30, type=int, help='Attempts to send data when sending failes (Defaults to 30)')
parser.add_argument('-t', '--timeout', default=60, type=int, help='Timeout between resend attempts (Seconds. Defaults to 60. If attempts is reached script will die)')
parser.add_argument('-c', '--config', default = None, help = 'config file path')
args = parser.parse_args()

# Factor in sleep for bandwidth checking
if args.interval >= 2:
    args.interval -= 2

def main(machine_ulimit = None, slack_token = None, slack_channel = None, verbose = True):
    # Hostname Info
    hostname = socket.gethostname()
    #print("Hostname:", hostname)

    # CPU Info
    cpu_count = psutil.cpu_count()
    cpu_usage = psutil.cpu_percent(interval=1)
    # print("CPU:\n\tCount:", cpu_count, "\n\tUsage:", cpu_usage)

    # Memory Info
    memory_stats = psutil.virtual_memory()
    memory_total = memory_stats.total
    memory_used = memory_stats.used
    memory_used_percent = memory_stats.percent
    # print("Memory:\n\tPercent:", memory_used_percent, "\n\tTotal:", memory_total / 1e+6, "MB", "\n\tUsed:", memory_used / 1e+6, "MB")

    # Disk Info
    disk_info = psutil.disk_partitions()
    #print("Disks:")
    disks = []
    for x in disk_info:
        # Try fixes issues with connected 'disk' such as CD-ROMS, Phones, etc.
        try:
            disk = {
                "name" : x.device,
                "mount_point" : x.mountpoint,
                "type" : x.fstype,
                "total_size" : psutil.disk_usage(x.mountpoint).total,
                "used_size" : psutil.disk_usage(x.mountpoint).used,
                "percent_used" : psutil.disk_usage(x.mountpoint).percent
            }

            disks.append(disk)

            # print("\tDisk name",disk["name"], "\tMount Point:", disk["mount_point"], "\tType",disk["type"], "\tSize:", disk["total_size"] / 1e+9,"\tUsage:", disk["used_size"] / 1e+9, "\tPercent Used:", disk["percent_used"])
        except:
            print("")

    # Bandwidth Info
    network_stats = get_bandwidth()
    # print("Network:\n\tTraffic in:",network_stats["traffic_in"] / 1e+6,"\n\tTraffic out:",network_stats["traffic_out"] / 1e+6)

    # Network Info
    nics = []
    # print("NICs:")
    for name, snic_array in psutil.net_if_addrs().items():
        # Create NIC object
        nic = {
            "name": name,
            "mac": "",
            "address": "",
            "address6": "",
            "netmask": ""
        }
        # Get NiC values
        for snic in snic_array:
            if snic.family == -1:
                nic["mac"] = snic.address
            elif snic.family == 2:
                nic["address"] = snic.address
                nic["netmask"] = snic.netmask
            elif snic.family == 23:
                nic["address6"] = snic.address
        nics.append(nic)
        # print("\tNIC:",nic["name"], "\tMAC:", nic["mac"], "\tIPv4 Address:",nic["address"], "\tIPv4 Subnet:", nic["netmask"], "\tIPv6 Address:", nic["address6"])

    # Platform Info
    system = {
        "name" : platform.system(),
        "version" : platform.release()
    }
    # print("OS:\n\t",system["name"],system["version"])

    # Time Info
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    uptime = int(time.time() - psutil.boot_time())
    # print("System Uptime:\n\t",uptime)

    # System UUID
    sys_uuid = uuid.getnode()

    # Set Machine Info
    machine = {
    	"hostname" : hostname,
		"uuid" : sys_uuid,
        "system" : system,
        "uptime" : uptime,
    	"cpu_count" : cpu_count,
    	"cpu_usage" : cpu_usage,
    	"memory_total" : memory_total,
    	"memory_used" : memory_used,
    	"memory_used_percent" : memory_used_percent,
    	"drives" : disks,
    	"network_up" : network_stats["traffic_out"],
    	"network_down" : network_stats["traffic_in"],
        "network_cards": nics,
        "timestamp" : timestamp
    }

    data = json.dumps(machine)
    if verbose:
        print("\nData:")
        pprint.pprint(machine, indent=4)

    if machine_ulimit:
        l_checks = [ machine[k] > float(v)
            for k, v in machine_ulimit.items()]
        if any(l_checks):
            send_slack_data(endpoint = args.endpoint, token = slack_token,
                channel = slack_channel,
                str_msg = format_machine_data(machine,
                    l_keys= ["hostname", "system", "uptime", "cpu_count", "cpu_usage",
                        "memory_used_percent", "timestamp"]
                    ),
                title = f'Server Monitor LIMIT ({list(machine_ulimit.keys())}) REACHED')
    else:
        #send_data(data)
        if slack_token:
            send_slack_data(endpoint = args.dest, token = slack_token,
                channel = slack_channel, str_msg = format_machine_data(machine),
                title = 'Server Monitor {args.interval}s update')

def get_bandwidth():
    # Get net in/out
    net1_out = psutil.net_io_counters().bytes_sent
    net1_in = psutil.net_io_counters().bytes_recv

    time.sleep(1)

    # Get new net in/out
    net2_out = psutil.net_io_counters().bytes_sent
    net2_in = psutil.net_io_counters().bytes_recv

    # Compare and get current speed
    if net1_in > net2_in:
        current_in = 0
    else:
        current_in = net2_in - net1_in

    if net1_out > net2_out:
        current_out = 0
    else:
        current_out = net2_out - net1_out

    network = {"traffic_in" : current_in, "traffic_out" : current_out}
    return network

def format_machine_data(machine_data, l_keys = None):
    if l_keys:
        machine_data = {k : v for k, v in machine_data.items() if k in l_keys}
    text = ''
    for k, v in machine_data.items():
        text += f"*{k}* : `{v}`\n"
    return text

def send_data(data, endpoint = args.dest, attempts = args.attempts):
    '''
    # Attempt to send data up to 30 times
    # endpoint = monitoring server
    '''
    if not endpoint:
        return None
    for attempt in attempts:
        try:
            response = requests.post(url = endpoint, data = data)
            print("\nPOST:")
            print("Response:", response.status_code)
            print("Headers:")
            pprint.pprint(response.headers)
            print("Content:", response.content)
            # Attempt printing response in JSON if possible
            try:
                print("JSON Content:")
                pprint.pprint(response.json())
            except:
                print("No JSON content")
            break
        except requests.exceptions.RequestException as e:
            print("\nPOST Error:\n",e)
            # Sleep 1 minute before retrying
            time.sleep(args.timeout)
    else:
        # If no connection established for attempts*timeout, kill script
        exit(0)

def retry_if_result_none(result):
    """
	Return True if we should retry (in this case when result is None), False otherwise
	"""
    return result is None

@retry(stop_max_attempt_number = args.attempts, retry_on_result = retry_if_result_none)
def send_slack_data(endpoint, token, channel, str_msg, title = ''):
    headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
    payload = {"channel": channel, "text": f"*{title}*\n{str_msg}"}
    try:
        response = requests.post( url = endpoint, data = json.dumps(payload), headers = headers)
        if response.status_code != 200:
            print(f'send_slack_data: POST response has status code {response.status_code}')
            return None
        else:
            return True
    except requests.exceptions.RequestException as e:
        print(f'send_slack_data: POST error: {e}')
        return None

while True:
    # read config file
    if args.config:
        with open(args.config, 'r') as ymlfile:
            cfg = yaml.load(ymlfile, Loader = yaml.BaseLoader)
            cfg = cfg['dev']['environment']
        args.interval = int(cfg['interval'])
        args.attempts = int(cfg['attempts'])
        args.timeout = int(cfg['timeout'])
        args.dest = cfg['dest']
        main(machine_ulimit=cfg['machine_ulimit'], slack_token = cfg['slack']['token'],
            slack_channel = cfg['slack']['channel'], verbose=True)
    else:
        main(verbose = True)
    print("-----------------------------------------------------------------")
    time.sleep(args.interval)
