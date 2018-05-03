import requests
from charmhelpers.core import unitdata
from charmhelpers.core.hookenv import log
try:
    import grpc
    from pyhelm.repo import from_repo
    from pyhelm.chartbuilder import ChartBuilder
    from pyhelm.tiller import Tiller
except ImportError:
    pass


def get_tiller():
    """
    Creates a tiller object.
    """
    tiller_host, tiller_port = unitdata.kv().get('tiller-service').split(':')
    return Tiller(host=tiller_host, port=tiller_port)


###########################################################
# The following methods return a status. The status codes can be found here:
# https://github.com/tengu-team/pyhelm/blob/master/hapi/release/status_pb2.py#L31
###########################################################


def install_release(chart_name, repo, namespace):
    """
    Installs a chart.

    Args:
        chart_name (str)
        repo (str)
        namespace (str)
    Returns:
        {
            'release': Name of the helm release,
            'status': Status of the installation
        }
    """
    try:
        chart_path = from_repo(repo, chart_name)
    except requests.RequestException as e:
        log(e)
        return {
            'Error': 'Helm repository unreachable.',
        }
    chart = ChartBuilder({
        'name': chart_name,
        'source': {
            'type': 'directory',
            'location': chart_path,
        },
    })
    try:
        tiller = get_tiller()
        response = tiller.install_release(chart.get_helm_chart(),
                                        dry_run=False,
                                        namespace=namespace)
        status_code = response.release.info.status.code
        return {
            'release': response.release.name,
            'status': response.release.info.status.Code.Name(status_code),
        }
    except grpc.RpcError as e:
        log(e.details())
        status_code = e.code()
        if grpc.StatusCode.UNAVAILABLE == status_code:
            return {
                'Error': 'Tiller unreachable.',
            }


def status_release(release):
    """
    Query the status of a release.

    Args:
        release (str): name of the release
    Returns:
        {
            'release': Name of the helm release,
            'status': Status of the installation (ex. DEPLOYED),
            'resources': Human readable helm resources output.
        }
        Or None if the status can not be retrieved.
    """
    try:
        tiller = get_tiller()
        response = tiller.get_release_status(name=release)
        status_code = response.info.status.code
        return {
            'release': release,
            'status': response.info.status.Code.Name(status_code),
            'resources': response.info.status.resources,
        }
    except grpc.RpcError as e:
        log(e.details())
        status_code = e.code()
        if grpc.StatusCode.UNAVAILABLE == status_code:
            return {
                'Error': 'Tiller unreachable.',
            }
        if grpc.StatusCode.UNKNOWN == status_code:
            return None


def uninstall_release(release):
    """
    Uninstall a release.

    Args:
        release (str): name of the release
    Returns:
        True | False
    """
    try:
        tiller = get_tiller()
        response = tiller.uninstall_release(release=release)
        return True
    except grpc.RpcError as e:
        log(e.details())
        status_code = e.code()
        if grpc.StatusCode.UNAVAILABLE == status_code:
            return {
                'Error': 'Tiller unreachable.',
            }
        else:
            return False
