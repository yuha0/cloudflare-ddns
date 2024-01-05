import os
import logging
LOG_LEVELS = {
        'debug': logging.DEBUG,
        'info': logging.INFO,
        'warning': logging.WARNING,
        'error': logging.ERROR,
}

logging.basicConfig(
        level=LOG_LEVELS[os.environ.get('APP_LOGLEVEL', 'info')],
        format='%(levelname).1s%(asctime)s %(filename)s:%(lineno)d] %(message)s',
        datefmt='%y%m%d %H:%M:%S')
import argparse
import sys
import json
import requests
import uuid
import time
from multiprocessing import Process
from datetime import datetime
from functools import partial

from leaderelection import LeaderElectionClient
from cloudflare import CloudflareClient

from prometheus_client import start_http_server, Gauge


def ddns(cf_client, subdomains, interval):
    start_http_server(2157)
    ip_status = Gauge("ddns_ip_status", "status of detected IP address", ["type", "ip", "proxied"])
    cf_client.set_metrics(ip_status)
    while True:
        cf_client.reconcile_all(subdomains)
        time.sleep(interval)


def on_start_leading(proc, cf_client, subdomains, interval):
    proc = Process(target=ddns, args=(cf_client, subdomains, interval))
    proc.start()


def on_stop_leading(proc):
    proc.terminate()


def get_client_id():
    pod_env = "POD_NAME"
    if pod_env in os.environ:
        client_id = os.environ[pod_env]
        logging.info(
            "Set client ID according to environment variable '%s': '%s'",
            pod_env,
            client_id,
        )
    else:
        client_id = uuid.uuid4()
        logging.warning(
            "Environment variable '%s' not found. Set election candidate ID to UUID '%s'",
            pod_env,
            client_id,
        )
    return client_id


def get_config():
    # cli args
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s",
        "--subdomains",
        type=str,
        default="",
        help="comma-separated subdomains, without base domain name suffix",
    )
    parser.add_argument(
        "-p", "--proxied", action="store_true", help="Enable Cloudflare Proxy"
    )
    parser.add_argument(
        "-i", "--interval", type=int, default=300, help="IP address check interval"
    )
    parser.add_argument(
        "--ipv4", action="store_true", help="Attempt to detect ipv4 public IP"
    )
    parser.add_argument(
        "--ipv6", action="store_true", help="Attempt to detect ipv6 public IP"
    )
    parser.add_argument("--purge", action="store_true", help="Attempt to delete stale records.")
    parser.add_argument(
        "--election-lock-name",
        type=str,
        default="ddns-leader-election",
        help="Name of the ConfigMap object for leader election",
    )
    parser.add_argument(
        "--election-lock-namespace",
        type=str,
        default="",
        help="Namespace for the leader election ConfigMap object",
    )
    parser.add_argument("--election-lease-duration", type=int, default=15)
    parser.add_argument("--election-renew-deadline", type=int, default=10)
    args = vars(parser.parse_args())

    args["subdomains"] = args["subdomains"].strip(",").split()
    logging.warning("cli arguments: %s", args)

    # env vars
    envvars = {
        "zone_id": os.environ.get("CF_ZONE_ID", None),
        "api_token": os.environ.get("CF_API_TOKEN", None),
        "api_key": os.environ.get("CF_API_KEY", None),
        "api_email": os.environ.get("CF_API_EMAIL", None),
    }

    return {
        **args,
        **envvars,
    }


def process_config(config):
    assert (
        config["interval"] > config["election_renew_deadline"]
    ), "DDNS update interval needs to be greater than election-renew-deadline to prevent stale leader from hitting cloudflare API"
    assert (
        config["election_lease_duration"] > config["election_renew_deadline"]
    ), "election-lease-duration needs to be greater than election-renew-deadline, so that renews happen before expiration"
    assert config["ipv4"] or config["ipv6"], "one of ipv4 and ipv6 has to be enabled"
    assert config['api_token'] or (config['api_key'] and config['api_email']), "Failed to detect cloud flare API credentials from environment variables"

    client_id = get_client_id()
    cf_config = {
        "client_id": client_id,
        "zone_id": config["zone_id"],
        "proxied": config["proxied"],
        "ipv4": config["ipv4"],
        "ipv6": config["ipv6"],
        "purge": config["purge"],
        "authentication": {
            "api_token": config["api_token"],
            "api_key": config["api_key"],
            "api_email": config["api_email"],
        },
    }
    le_config = {
        "candidate_id": client_id,
        "lock_name": config["election_lock_name"],
        "lock_ns": config["election_lock_namespace"],
        "lease_duration": config["election_lease_duration"],
        "renew_deadline": config["election_renew_deadline"],
    }
    return (cf_config, le_config)


if __name__ == "__main__":
    config = get_config()
    cf_config, le_config = process_config(config)
    cf_client = CloudflareClient(**cf_config)

    ddns_proc = None
    onstart_cb = partial(on_start_leading, proc=ddns_proc, cf_client=cf_client, subdomains=config['subdomains'], interval=config['interval'])
    onstop_cb = partial(on_stop_leading, proc=ddns_proc)

    le_client = LeaderElectionClient(onstart=onstart_cb, onstop=onstop_cb, **le_config)

    le_client.run()
