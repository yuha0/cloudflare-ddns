import logging
from kubernetes import client, config
from kubernetes.leaderelection import leaderelection, electionconfig
from kubernetes.leaderelection.resourcelock.configmaplock import ConfigMapLock


class LeaderElectionClient:
    def __init__(
        self,
        candidate_id,
        lock_name="leader-election",
        lock_ns="",
        lease_duration=15,
        renew_deadline=10,
        onstart=lambda *args: None,
        onstop=lambda *args: None,
        kubeconfig="~/.kube/config",
    ):
        try:
            config.load_incluster_config()
            self.incluster = True
        except config.config_exception.ConfigException:
            self.incluster = False
            config.load_kube_config(config_file=kubeconfig)
        self.kclient = client.CoreV1Api()
        self.ns = self._get_namespace()
        lock_ns = lock_ns if lock_ns else self.ns
        logging.info("Set election namespace to '%s'", lock_ns)
        logging.info("Set election lock to ConfigMap '%s'", lock_name)
        self.candidate_id = candidate_id
        election_config = electionconfig.Config(
            ConfigMapLock(lock_name, lock_ns, self.candidate_id),
            lease_duration,
            renew_deadline,
            retry_period=5,
            onstarted_leading=self._prepare_callback(isleader=True, cb=onstart),
            onstopped_leading=self._prepare_callback(isleader=False, cb=onstop),
        )
        self.election = leaderelection.LeaderElection(election_config)

    def _get_namespace(self):
        if self.incluster:
            with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as ns:
                namespace = ns.read()
        else:
            namespace = config.list_kube_config_contexts()[1]["context"]["namespace"]
        return namespace

    def _prepare_callback(self, isleader, cb):
        def wrapper():
            logging.info("I am %s", "the leader" if isleader else "a follower")
            patches = [
                {
                    "op": "replace",
                    "path": "/metadata/labels/primary",
                    "value": str(isleader).lower(),
                }
            ]
            self.kclient.patch_namespaced_pod(self.candidate_id, self.ns, body=patches)
            cb()

        return wrapper

    def run(self):
        self.election.run()
