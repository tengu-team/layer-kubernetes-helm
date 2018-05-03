# Layer-kubernetes-helm

Provides a way to deploy [Helm charts](https://helm.sh/) to a Kubernetes cluster.


## Caveats

- Waiting on [issue](https://github.com/juju-solutions/charms.reactive/issues/170) to fix deletion of installed charts.
- The returned status will not wait until all resources are ready and will not resend a status update until the requested charts installs changes.



## Authors
This software was created in the [IDLab research group](https://www.ugent.be/ea/idlab/en) of [Ghent University](https://www.ugent.be/en) in Belgium. This software is used in [Tengu](https://tengu.io), a project that aims to make experimenting with data frameworks and tools as easy as possible.
- Sander Borny <sander.borny@ugent.be>