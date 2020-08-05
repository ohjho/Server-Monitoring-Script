import argparse, socket, time, json, datetime, platform, psutil, requests, pprint, uuid
from retrying import retry
import yaml, pynvml

###############################################################################
# GPU
###############################################################################
HAS_CUDA = False
try:
    pynvml.nvmlInit()
    HAS_CUDA = True
except:
    HAS_CUDA = False

def gpu_count():
    if not HAS_CUDA:
        return 0
    else:
        return pynvml.nvmlDeviceGetCount()

def gpu_mem_used_pct():
    '''
    return a list of percentage of memory used
    '''
    if gpu_count() == 0:
        return []

    l_GPUs = list(range(gpu_count()))
    l_mm = []
    for i in l_GPUs:
        h = pynvml.nvmlDeviceGetHandleByIndex(i)
        info = pynvml.nvmlDeviceGetMemoryInfo(h)
        l_mm.append(info.used / info.total)
    return l_mm

###############################################################################
# Machine Info
###############################################################################
def get_machine_data(verbose = False):
    '''
    return details about the machine in JSON format
    '''
    # Hostname Info
    hostname = socket.gethostname()

    # CPU Info
    cpu_count = psutil.cpu_count()
    cpu_usage = psutil.cpu_percent(interval=1)

    # Memory Info
    memory_stats = psutil.virtual_memory()
    memory_total = memory_stats.total
    memory_used = memory_stats.used
    memory_used_percent = memory_stats.percent

    # Disk Info
    disk_info = psutil.disk_partitions()
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
        except:
            print("")

    # Bandwidth Info
    network_stats = get_bandwidth()

    # Network Info
    nics = []
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

    # Platform Info
    system = {
        "name" : platform.system(),
        "version" : platform.release()
    }

    # Time Info
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    uptime = int(time.time() - psutil.boot_time())

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
        "root_drive_used_percent": psutil.disk_usage("/").percent,
    	"network_up" : network_stats["traffic_out"],
    	"network_down" : network_stats["traffic_in"],
        "network_cards": nics,
        "timestamp" : timestamp
    }
    if HAS_CUDA:
        machine["gpu_memory_max_used_percent"] = round(max(gpu_mem_used_pct())* 100,2)

    if verbose:
        print("\nData:")
        pprint.pprint(machine, indent=4)
    return machine

def get_bandwidth():
    '''
    Get net in/out
    '''
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
    '''
    take machine's JSON and return as formatted string
    '''
    if l_keys:
        machine_data = {k : v for k, v in machine_data.items() if k in l_keys}
    text = ''
    for k, v in machine_data.items():
        text += f"*{k}* : `{v}`\n"
    return text

###############################################################################
# Sending Alerts
###############################################################################
def send_data(data, endpoint = None, attempts = 30, timeout = 60):
    '''
    # [DEPRECATED]
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
            time.sleep(timeout)
    else:
        # If no connection established for attempts*timeout, kill script
        exit(0)

def retry_if_result_none(result):
    """
	Return True if we should retry (in this case when result is None), False otherwise
	"""
    return result is None

@retry(stop_max_attempt_number = 3, retry_on_result = retry_if_result_none)
def send_slack_data(endpoint, token, channel, str_msg, title = ''):
    '''
    send a slack message and return true if successful
    '''
    headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
    text = f"*{title}*\n---------------------\n{str_msg}" if title else str_msg
    payload = {"channel": channel, "text": text}
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

###############################################################################
# App Entry Point
###############################################################################
def main(endpoint = None, machine_ulimit = None, slack_token = None, slack_channel = None, verbose = True):
    '''
    '''
    if endpoint: # then slack data is required
        if not slack_token or not slack_channel:
            raise RuntimeError(f'server_monitor.main: args slack_token and slack_channel must be provided with endpoint={endpoint}')

    machine = get_machine_data(verbose = verbose)

    if machine_ulimit: # only send Slack Message if limit is reach
        l_checks = [ machine[k] > float(v)
            for k, v in machine_ulimit.items()]
        if any(l_checks) and endpoint:
            l_limit_reached = [k for c, k in zip(l_checks, list(machine_ulimit.keys())) if c]

            send_slack_data(endpoint = endpoint, token = slack_token,
                channel = slack_channel,
                str_msg = format_machine_data(machine,
                    l_keys= ["hostname", "system", "uptime", "cpu_count", "cpu_usage",
                        "memory_used_percent", "root_drive_used_percent", "gpu_memory_max_used_percent",
                        "timestamp"]
                    ),
                title = f'Server Monitor LIMIT ({", ".join(l_limit_reached)}) REACHED')
    else: # send everything
        if endpoint:
            send_slack_data(endpoint = endpoint, token = slack_token,
                channel = slack_channel, str_msg = format_machine_data(machine),
                title = 'Server Monitor Update')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Monitoring script to send system info to Slack')
    parser.add_argument('-d', '--dest', default= None, help='API Endpoint for Monitoring Data (Defaults to None)')
    parser.add_argument('-i', '--interval', default=5, type=int, help='Interval between checks (Seconds. Defaults to 5 seconds)')
    parser.add_argument('-a', '--attempts', default=30, type=int, help='Attempts to send data when sending failes (Defaults to 30)')
    parser.add_argument('-t', '--timeout', default=60, type=int, help='Timeout between resend attempts (Seconds. Defaults to 60. If attempts is reached script will die)')
    parser.add_argument('-c', '--config', default = None, help = 'optional config file path')
    parser.add_argument('--slack_token', default = None, help = 'optional slack token')
    parser.add_argument('--slack_channel', default = None, help = 'optional slack channel')
    args = parser.parse_args()

    # Factor in sleep for bandwidth checking
    if args.interval >= 2:
        args.interval -= 2

    while True:
        # read config file
        if args.config:
            with open(args.config, 'r') as ymlfile:
                cfg = yaml.load(ymlfile, Loader = yaml.BaseLoader)
                cfg = cfg['dev']['environment']

            args.interval = int(cfg['interval'])
            # --- DEPRECATED ---
            # args.attempts = int(cfg['attempts'])
            # args.timeout = int(cfg['timeout'])
            main(endpoint = cfg['dest'], machine_ulimit=cfg['machine_ulimit'],
                slack_token = cfg['slack']['token'],
                slack_channel = cfg['slack']['channel'], verbose=True)
        else:
            main(verbose = True, endpoint = args.dest, slack_token = args.slack_token,
                    slack_channel = args.slack_channel)
        print(f"--------------------- Server Monitor {args.interval}s Update ----------------------")
        time.sleep(args.interval)
