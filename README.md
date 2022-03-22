# Cloudflare DDNS

[Dynamic DNS](https://en.wikipedia.org/wiki/Dynamic_DNS) provider for Cloudflare.

This project is inspired by [timothymiller/cloudflare-ddns](https://github.com/timothymiller/cloudflare-ddns/tree/a9d25c743a2341a37e77f79abcbdb9a900528f92), which is a solid script. Comparing to that, this project has a few opinionated takes:

1. The code base is more modularized and without magic global variables, makes it easier to extend. However, now you need to deal with more than one `py` file, hence more difficult to deploy.
2. It implements Kubernetes native leader election, so you can run multiple replicas on more than one hosts. Only the single leader instance can run the actual business logic, and others are hot-standby instances ready to take over in case of failure.
3. [distroless](https://github.com/GoogleContainerTools/distroless) based image. Fatter than [alpine](https://github.com/timothymiller/cloudflare-ddns/blob/a9d25c743a2341a37e77f79abcbdb9a900528f92/README.md?plain=1#L30), but arguably better in some cases.
4. [DNS Registry](#dns-registry) for traceability and housekeeping.

## TODO:

- Expose prometheus metrics.

## DNS Registry

The mission of this project is to watch public IP updates by your ISP and update the public `A`/`AAAA` records accordingly. It should not touch DNS records managed by other systems(unless is explicitly told by users), but at the same time, has the confident to delete records it knowns that itself is the manager. 

Inspired by [kubernetes-sigs/external-dns](https://github.com/kubernetes-sigs/external-dns)'s [Record Registry](https://github.com/kubernetes-sigs/external-dns/blob/c4d978498c0eee21364966c7ae664e56032ac00b/docs/proposal/registry.md), for every `A`/`AAAA` this program creates, it also creates a `TXT` record with some content like this:

```
"heritage=cloudflare-ddns,cloudflare-ddns/owner=<owner-id>"
```

where `<owner-id>` is either the pod's name, or an UUID generaged on start.

If you run the script with `--subdomains foo,bar` today, and restart it tomorrow with a different list of subdomains `--subdomains baz`, the `A`, `AAAA`, `TXT` records for `foo` and `bar` will all be deleted.

## Configuration

### Environment Variables

Cloudflare API credentials need to be available as environemtn variables before start.

- `CF_ZONE_ID`: DNS zone ID. This information is not sensitive, but I thought it's nice to have all Cloudflare information in the same place. This ID is a 32-character random string, so it is not for humans anyways.
- `CF_API_TOKEN`: Preferred authentication method.
- `CF_API_KEY`: Alternative authentication method. To use this, `CF_API_EMAIL` must be set as well.
- `CF_API_EMAIL`: Required if `CF_API_KEY` is set.

### Command line arguments

```
usage: ddns.py [-h] [-s SUBDOMAINS] [-p] [-i INTERVAL] [--ipv4] [--ipv6]
               [--purge] [--election-lock-name ELECTION_LOCK_NAME]
               [--election-lock-namespace ELECTION_LOCK_NAMESPACE]
               [--election-lease-duration ELECTION_LEASE_DURATION]
               [--election-renew-deadline ELECTION_RENEW_DEADLINE]

optional arguments:
  -h, --help            show this help message and exit
  -s SUBDOMAINS, --subdomains SUBDOMAINS
                        comma-separated subdomains, without base domain name
                        suffix
  -p, --proxied         Enable Cloudflare Proxy
  -i INTERVAL, --interval INTERVAL
                        IP address check interval
  --ipv4                Attempt to detect ipv4 public IP
  --ipv6                Attempt to detect ipv6 public IP
  --purge               Attempt to delete stale records.
  --election-lock-name ELECTION_LOCK_NAME
                        Name of the ConfigMap object for leader election
  --election-lock-namespace ELECTION_LOCK_NAMESPACE
                        Namespace for the leader election ConfigMap object
  --election-lease-duration ELECTION_LEASE_DURATION
  --election-renew-deadline ELECTION_RENEW_DEADLINE
```
