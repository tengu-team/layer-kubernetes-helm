import os
import json
import wget
from shutil import which
from subprocess import check_call, check_output, CalledProcessError
from charmhelpers.core import unitdata
from charmhelpers.core.hookenv import (
    log,
    status_set,
    config,
    charm_dir,
)
from charms.reactive import (
    when,
    when_not,
    set_flag,
    clear_flag,
    endpoint_from_flag,
    data_changed,
)
from charms.layer.k8shelpers import (
    get_worker_node_ips,
    create_resource_by_file,
    resource_exists_by_file,
    get_resource_by_file,
)
from charms.layer.helm_helpers import (
    install_release,
    uninstall_release,
    status_release,
)

# Add kubectl to PATH
os.environ['PATH'] += os.pathsep + os.path.join(os.sep, 'snap', 'bin')
conf = config()


# TODO should wait until k8s is ready
@when('leadership.is_leader')
@when_not('kubernetes-helm.installed')
def install_kubernetes_helm():
    # Install Helm client if needed
    if not which('helm'):
        wget.download(url='https://raw.githubusercontent.com/kubernetes/helm/master/scripts/get',
                      out='/home/ubuntu/get_helm.sh')
        os.chmod('/home/ubuntu/get_helm.sh', 0o700)
        try:
            check_call(['bash', '/home/ubuntu/get_helm.sh'])
        except CalledProcessError as e:
            log(e)
            status_set('blocked', 'Failed installing Helm client')
            return
    # Try to install Tiller
    # CAUTION: This will install Tiller with no security context !  
    try:
        check_call(['helm', 'init'])
    except CalledProcessError as e:
        log(e)
        status_set('blocked', 'Error installing Tiller')
        return
    # Install nodeport service for tiller
    # Save host:port in unitdata 'tiller-service'
    tiller_config_path = charm_dir() + '/files/tiller-service.yaml'
    if resource_exists_by_file(tiller_config_path):
        tiller_service = get_resource_by_file(tiller_config_path)
        if not tiller_service:
            log('Failed to fetch tiller service info')
            status_set('blocked', 'Failed to fetch tiller service info')
            return
    else:
        tiller_service = create_resource_by_file(tiller_config_path)
    if not tiller_service:
        log('Failed to create tiller service')
        status_set('blocked', 'Failed to create tiller service')
        return
    tiller_nodeport = tiller_service['spec']['ports'][0]['nodePort']
    unitdata.kv().set('tiller-service', get_worker_node_ips()[0] + ':' + str(tiller_nodeport))   
    # Install pyhelm lib
    try:
        check_call(['git',
                    'clone',
                    'https://github.com/tengu-team/pyhelm.git',
                    '/home/ubuntu/pyhelm'])
        check_call(['python3', '/home/ubuntu/pyhelm/setup.py', 'install'])
    except CalledProcessError as e:
        log(e)
        status_set('blocked', 'Failed to install pyhelm library')
        return
    set_flag('kubernetes-helm.installed')


@when('endpoint.helm.new-chart-requests',
      'kubernetes-helm.installed',
      'leadership.is_leader')
def helm_requested():
    """
    chart_requests format
    {
        'model_uuid_unit_name': [
        {
            'name': 'chart_name1',
            'repo': 'url',
            },
            {
            'name': 'chart_name2',
            'repo': 'url',
            }
        ],
    }

    previous_requests / live format
    {
        'model_uuid_unit_name': {
            'chart_name1': {
                'release': 'release_name1',
                'status': 'DEPLOYED',
            }
            'chart_name2': {
                'release': 'release_name2',
                'status': 'DEPLOYED',
            }
        }
    }
    """
    endpoint = endpoint_from_flag('endpoint.helm.new-chart-requests')
    namespace = conf.get('namespace', 'default')
    chart_requests = endpoint.get_chart_requests()
    previous_requests = update_release_info(unitdata.kv().get('live-releases', {}))
    live = {}
    for unit in chart_requests.keys():
        live[unit] = {}
        # Check if request already installed
        for chart_request in chart_requests[unit]:
            if (unit in previous_requests
                and chart_request['name'] in previous_requests[unit]):
                # Add wanted release to live
                live[unit][chart_request['name']] = previous_requests[unit][chart_request['name']]
                # Remove wanted release from previous_requests,
                # so only non wanted releases remain for easy deletion        
                del previous_requests[unit][chart_requests[unit]['name']]
        else:
            # Install new chart requests and add them to live
            for chart_request in chart_requests[unit]:
                if chart_request['name'] not in live[unit]:
                    release = install_release(chart_request['name'],
                                            chart_request['repo'],
                                            namespace)
                    live[unit][chart_request['name']] = release
    # Uninstall unwanted charts, those remaining in previous_requests
    for unit in previous_requests:
        for chart_name in previous_requests[unit]:
            uninstall_release(previous_requests[unit][chart_name]['release'])
    # Save the live update for next invocation
    unitdata.kv().set('live-releases', live)
    # Return a status update to connected units
    endpoint.send_status(live)
    clear_flag('endpoint.helm.new-chart-requests')


def update_release_info(requests):
    """
    Update the status of helm releases.
    """
    updated = requests
    for unit in requests:
        for chart in requests[unit]:
            release = requests[unit][chart]['release']
            updated[unit]['status'] = status_release(release)
    return updated