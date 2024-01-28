import logging
import requests
from itertools import compress

from prometheus_client import Gauge

IP_STATUS = Gauge("ddns_ip_status", "status of detected IP address", ["type", "ip", "proxied"])


class CloudflareClient:
    API_BASE = "https://api.cloudflare.com/client/v4/zones"
    OWNER_REF = '"heritage=cloudflare-ddns,cloudflare-ddns/owner={}"'
    TRACE = {
        "ipv4": "https://1.1.1.1/cdn-cgi/trace",
        "ipv6": "https://[2606:4700:4700::1111]/cdn-cgi/trace",
    }

    def __init__(self, client_id, authentication, zone_id, proxied, ipv4, ipv6, purge):
        self.client_id = client_id
        self.zone_id = zone_id
        self.proxied = proxied
        self.ipv4 = ipv4
        self.ipv6 = ipv6
        self.purge = purge
        self.auth_header = self._auth_header(authentication)
        self.base_domain = self._get_base_domain()
        self.ips = {"ipv4": None, "ipv6": None}
        self.expired_ts = set()


    def _auth_header(self, authentication):
        try:
            header = {"Authorization": f"Bearer {authentication['api_token']}"}
        except KeyError:
            header = {
                "X-Auth-Email": authentication["api_key"],
                "X-Auth-Key": authentication["api_email"],
            }
        return header

    def _get_base_domain(self):
        return requests.get(
            f"{self.API_BASE}/{self.zone_id}", headers=self.auth_header
        ).json()["result"]["name"]

    def _get_records(self, rtype):
        params = {
            "per_page": 100,
            "type": rtype,
        }
        return requests.get(
            f"{self.API_BASE}/{self.zone_id}/dns_records",
            headers=self.auth_header,
            params=params,
        ).json()["result"]

    def _get_subdomain(self, fqdn):
        if fqdn.endswith(self.base_domain):
            return fqdn[: -len(self.base_domain)]

    def refresh_ips(self):
        changed = False
        logging.info("Refreshing IP addresses")
        for req in compress(self.TRACE.keys(), [self.ipv4, self.ipv6]):
            r = requests.get(self.TRACE[req]).text.split("\n")
            ip = dict(l.split("=") for l in r[:-1])["ip"]
            if self.ips[req] != ip:
                logging.info(
                    "Updating recorded %s address from %s to %s", req, self.ips[req], ip
                )
                self.ips[req] = ip
                changed = True
        if not changed:
            logging.info("Public IP addresses haven't been changed since last update")

    def get_target_records(self, rtypes):
        results = {rtype: {} for rtype in rtypes}
        for rtype in rtypes:
            records = self._get_records(rtype)
            results[rtype] = {r["name"]: r for r in records}
        return results

    def _generate_record_a_aaaa(self, rtype, target, ip):
        return {
            "type": rtype,
            "name": target,
            "content": ip,
            "proxied": self.proxied,
            "ttl": 1,
        }

    def _generate_record_txt(self, a_aaaa_record):
        return {
            "type": "TXT",
            "name": a_aaaa_record["name"],
            "content": self.OWNER_REF.format(self.client_id),
            "proxied": False,
            "ttl": 1,
        }

    def update_record(self, record, rid=""):
        if rid:
            # update existing record
            requests.put(
                f"{self.API_BASE}/{self.zone_id}/dns_records/{rid}",
                headers=self.auth_header,
                json=record,
            )
        else:
            # add new record
            requests.post(
                f"{self.API_BASE}/{self.zone_id}/dns_records/",
                headers=self.auth_header,
                json=record,
            )

    def delete_record(self, record):
        logging.warning("Deleting %s record %s: %s", record['type'], record['name'], record['content'])
        requests.delete(f"{self.API_BASE}/{self.zone_id}/dns_records/{record['id']}", headers=self.auth_header)

    def reconcile_record(self, desired, actual):
        if not actual:
            logging.info(
                "Adding new %s record for %s", desired["type"], desired["name"]
            )
            self.update_record(desired)
            if desired["type"] in {"A", "AAAA"}:
                IP_STATUS.labels(desired["type"], desired["content"], str(desired["proxied"]).lower()).set(1)
            return

        assert (
            desired["name"] == actual["name"]
        ), f"Cannot compare different fqdn records ('{desired['name']}' and '{actual['name']}')"
        assert (
            desired["type"] == actual["type"]
        ), f"Cannot compare different record types ('{desired['type']}' and '{actual['type']}')"

        if (
            desired["content"] == actual["content"]
            and desired["proxied"] == actual["proxied"]
        ):
            logging.info(
                "The %s record for %s is up to date", actual["type"], actual["name"]
            )
            if desired["type"] in {"A", "AAAA"}:
                IP_STATUS.labels(desired["type"], desired["content"], str(desired["proxied"]).lower()).set(1)
        else:
            logging.info("Updating record: '%s' -> '%s'", actual, desired)
            self.update_record(desired, actual["id"])
            if desired["type"] in {"A", "AAAA"}:
                IP_STATUS.labels(desired["type"], desired["content"], str(desired["proxied"]).lower()).set(1)
            if actual["type"] in {"A", "AAAA"}:
                IP_STATUS.labels(actual["type"], actual["content"], str(actual["proxied"]).lower()).set(0)
                self.expired_ts.add((actual["type"], actual["content"], str(actual["proxied"]).lower()))

    def reconcile_all(self, subdomains=[""]):
        logging.info("Start record reconciliation")
        for l in self.expired_ts:
            IP_STATUS.remove(list(l))

        self.refresh_ips()

        fqdns = {
            f"{s}.{self.base_domain}" if s != "" else self.base_domain
            for s in subdomains
        }
        records = self.get_target_records(["A", "AAAA", "TXT"])

        logging.info("Updating configured domains")
        for fqdn in fqdns:
            for r in [
                {"type": "A", "protocol": "ipv4"},
                {"type": "AAAA", "protocol": "ipv6"},
            ]:
                rtype = r["type"]
                proto = r["protocol"]
                if self.ips[proto] is not None:
                    desired = self._generate_record_a_aaaa(rtype, fqdn, self.ips[proto])
                    current = records[rtype].get(fqdn, None)
                    self.reconcile_record(desired, current)
                    if current:
                        del records[rtype][fqdn]
                    desired_txt = self._generate_record_txt(desired)
            current_txt = records["TXT"].get(fqdn, None)
            self.reconcile_record(desired_txt, current_txt)
            if current_txt:
                del records["TXT"][fqdn]

        logging.info("Checking out-of-sync records")
        for fqdn in records["TXT"]:
            txt_record = records["TXT"][fqdn]
            if "heritage=cloudflare-ddns" not in txt_record["content"]:
                continue
            a_record = records["A"].get(fqdn, None)
            aaaa_record = records["AAAA"].get(fqdn, None)
            for record in [a_record, aaaa_record, txt_record]:
                if record is not None:
                    logging.warning(
                        "%s %s record '%s'",
                        "Deleting" if self.purge else "Considering deleting",
                        record["type"],
                        record["name"],
                    )
                    self.delete_record(record)
        logging.info("Finished reconciliation")
