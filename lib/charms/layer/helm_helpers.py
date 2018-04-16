from charmhelpers.core import unitdata
try:
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
            'name': Name of the helm release,
            'status': Status of the installation 
        }
    """
    chart_path = from_repo(repo, chart_name)
    chart = ChartBuilder({
        'name': chart_name,
        'source': {
            'type': 'directory',
            'location': chart_path,
        },
    })    
    tiller = get_tiller()
    response = tiller.install_release(chart.get_helm_chart(),
                                      dry_run=False,
                                      namespace=namespace)
    status_code = response.release.info.status.code
    return {
        'name': response.release.name,
        'status': response.release.info.status.Code.Name(status_code),
    }


def status_release(release):
    """
    Query the status of a release.

    Args:
        release (str): name of the release
    Returns:
        {
            'name': Name of the helm release,
            'status': Status of the installation (ex. DEPLOYED)
        }
    """
    tiller = get_tiller()
    response = tiller.get_release_status(name=release)
    status_code = response.info.status.code
    return {
        'name': release,
        'status': response.info.status.Code.Name(status_code),
    }


def uninstall_release(release):
    """
    Uninstall a release.

    Args:
        release (str): name of the release
    Returns:
        {
            'name': Name of the helm release,
            'status': Status of the installation (ex. DEPLOYED)
        }
    """
    tiller = get_tiller()
    response = tiller.uninstall_release(release=release)
    status_code = response.release.info.status.code
    return {
        'name': release,
        'status': response.release.info.status.Code.Name(status_code),
    }
