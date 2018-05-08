import os
import json
import wget
import copy
import pkgutil
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
    get_resource_by_name_type,
)
from charms.layer.kubernetes_helm import (
    install_release,
    uninstall_release,
    status_release,
)

# Add kubectl to PATH
os.environ['PATH'] += os.pathsep + os.path.join(os.sep, 'snap', 'bin')
conf = config()


@when('leadership.is_leader',
      'kubernetes.ready')
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
    else:
        tiller_service = create_resource_by_file(tiller_config_path)
    if not tiller_service:
        log('Failed to create tiller service')
        status_set('blocked', 'Failed to create tiller service')
        return
    tiller_nodeport = tiller_service['spec']['ports'][0]['nodePort']
    unitdata.kv().set('tiller-service', get_worker_node_ips()[0] + ':' + str(tiller_nodeport)) 
    # Install pyhelm lib if not installed yet
    if not pkgutil.find_loader('pyhelm'):
        wd = os.getcwd()        
        try:
            check_call(['git',
                        'clone',
                        'https://github.com/tengu-team/pyhelm.git',
                        '/home/ubuntu/pyhelm'])
            os.chdir('/home/ubuntu/pyhelm')
            check_call(['python3', 'setup.py', 'install'])
        except CalledProcessError as e:
            log(e)
            status_set('blocked', 'Failed to install pyhelm library')
            return
        os.chdir(wd)
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
                'resources': [],
            }
            'chart_name2': {
                'Error': 'Helm repository unreachable.',
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
    remove_installed_requests(chart_requests, previous_requests, live)
    install_requests(chart_requests, previous_requests, live, namespace)
    uninstall_requests(previous_requests)
    # Update live to get latest resource info from newly created resources
    live = update_release_info(live)
    # Save the live update for next invocation
    unitdata.kv().set('live-releases', live)
    # Return a status update to connected units
    endpoint.send_status(live)
    clear_flag('endpoint.helm.new-chart-requests') # TODO bij error zal de methode niet meer proberen uit te voeren, misschien is dit goed?


def update_release_info(requests):
    """
    Update the status of helm releases.
    """
    updated = copy.deepcopy(requests)
    for unit in requests:
        for chart in requests[unit]:
            if 'release' in requests[unit][chart]:
                release = requests[unit][chart]['release']
                release_status = status_release(release)
                if release_status:
                    updated[unit][chart]['status'] = release_status['status']
                    updated[unit][chart]['resources'] = \
                        extract_resources(release_status['resources'])
                else: 
                    # This means that a chart has been uninstalled manually
                    # Remove from the view so it can be reinstalled if needed
                    del updated[unit][chart]
    return updated


def extract_resources(resource_str):
    """
    Extract resource types and names from resource_str and 
    return the kubectl description.
    """
    index = None
    resource_type = ''
    resource_name = ''

    resources = {}

    for line in resource_str.split('\n'):
        if line.startswith('==>'):
            resource_type = line.split()[1].split('/')[1].lower().split('(')[0]
        elif resource_type and index == None:
            index = line.split().index('NAME')
        elif index is not None:
            resource_name = line.split()[index]
        if resource_type and resource_name:
            if resource_type not in resources:
                resources[resource_type] = []
            resources[resource_type].append(resource_name)
            resource_type = ''
            resource_name = ''
            index = None

    ret = []
    for resource_type in resources:
        for name in resources[resource_type]:
            resource = get_resource_by_name_type(name,
                        conf.get('namespace', 'default'),
                        resource_type)
            if resource:
                ret.append(resource)
    return ret


def remove_installed_requests(current_requests, previous_requests, live):
    """
    Remove requests from previous_requests if they are installed.
    A request is installed if it has a release name.
    """
    for unit in current_requests.keys():
        for chart_request in current_requests[unit]:
            if (unit in previous_requests
                and chart_request['name'] in previous_requests[unit]
                and 'release' in previous_requests[unit]):
                # Add wanted release to live
                live[unit][chart_request['name']] = \
                    previous_requests[unit][chart_request['name']]
                # Remove wanted release from previous_requests,
                # so only non wanted releases remain for easy deletion
                del previous_requests[unit][chart_request['name']]


def install_requests(current_requests, previous_requests, live, namespace):
    """
    Install all new requests or requests which do not have a release name.
    This will retry to install all failed install requests.
    """
    # Install new chart requests and add them to live
    # Retry errored requests
    for unit in current_requests.keys():
        for chart_request in current_requests[unit]:
            if (unit not in previous_requests
                or 'release' not in previous_requests[unit]):
                release = install_release(chart_request['name'],
                                        chart_request['repo'],
                                        namespace)
                live[unit][chart_request['name']] = release


def uninstall_requests(previous_requests):
    """
    Uninstall all releases which are present in previous_requests.
    """
    for unit in previous_requests:
        for chart_name in previous_requests[unit]:
            uninstall_release(previous_requests[unit][chart_name]['release'])


@when('endpoint.helm.status-update',
      'kubernetes-helm.installed',
      'leadership.is_leader')
def update_status_subscribers():
    endpoint = endpoint_from_flag('endpoint.helm.status-update')
    subs = endpoint.get_status_update_subscribers()
    if not subs:
        return
    previous_requests = unitdata.kv().get('live-releases', {})
    needed_requests = previous_requests
    for unit in previous_requests:
        if unit not in subs:
            del needed_requests[unit]
    live_subs = update_release_info(needed_requests)
    endpoint.send_status(live_subs)
