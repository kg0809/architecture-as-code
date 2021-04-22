from ruamel.yaml import YAML
import sys, os, stat, shutil, re, json
from pathlib import Path
yaml=YAML()
deployment_files_folder = 'deployment_files'
import redis

# architecture diagram generation
from diagrams import Diagram, Edge, Cluster
from diagrams.onprem.queue import Kafka
from diagrams.onprem.container import Docker
from diagrams.programming.flowchart import Database
from diagrams.elastic.elasticsearch import Elasticsearch, Kibana, Logstash
from diagrams.programming.framework import React
from diagrams.programming.framework import Django
from diagrams.onprem.network import Nginx
from diagrams.aws.engagement import SimpleEmailServiceSesEmail
from diagrams.programming.flowchart import MultipleDocuments
from diagrams.onprem.compute import Server
from diagrams.onprem.client import Users
from diagrams.onprem.network import Internet
from diagrams.onprem.inmemory import Redis
from diagrams.onprem.network import Haproxy
from diagrams.custom import Custom

import pkg_resources
resource_package = __name__

class ArchitectureAsCode:
    prefix = ''
    redis_object = None
    yml = None
    edi = 0 # environment details index
    sdi = 0 # service details index
    ddi = 0 # deployment details index
    idi = 0 # ingress details index
    
    def __init__(self, prefix, debug=False):
        self.debug = debug
        self.prefix = prefix #.upper()
        self.redis_object = redis.Redis(host=os.getenv(self.prefix + '_UTIL_REDIS_SERVICE_SERVICE_HOST'), port=os.getenv(self.prefix + '_UTIL_REDIS_SERVICE_SERVICE_PORT'))
    
    def __call__(self, config_main_file_name='config_main.yaml', services_requiring_gpu=[], update_monitoring=False, non_gpu_environment='soe'):
        with open(config_main_file_name) as file:
            self.yml = list(yaml.load_all(file))

            # figure out which config is which
            for num, name in enumerate(self.yml, start=0):
                if name['kind'] == 'EnvironmentDetails':
                    self.edi = num
                elif name['kind'] == 'ServiceDetails':
                    self.sdi = num

            proxy_ports = {}
            # populate proxy port details, if any
            for service in self.yml[self.sdi]['services']:    
                if 'haproxy' in service['name']:
                    for container in service['containers']:
                        if 'port_mappings' in container:
                            for port_mapping in container['port_mappings']:
                                if 'name' in port_mapping:
                                    proxy_ports[port_mapping['name']] = port_mapping['target']

            # process environment details
            for environment in self.yml[self.edi]['environments']:
                env_path = os.path.join(deployment_files_folder, environment['name'])
                # delete folder and files if it exists
                if os.path.exists(env_path):
                    shutil.rmtree(env_path)
                os.makedirs(env_path)

                # write convenience script to pull images
                images_to_pull = []
                pull_latest_images_script_name = os.path.join(env_path, 'pull_latest_images.sh')
                pli = open(pull_latest_images_script_name, 'w')

                # iterate over each service and populate deployment files for current environment
                for service in self.yml[self.sdi]['services']:

                    # write docker run script
                    #   most services should only have one container.
                    #   if service has > 1 container, to create one script per container and add suffix
                    add_container_suffix = False
                    if len(service['containers']) > 1:
                        add_container_suffix = True
                    for container in service['containers']:
                        # only create dockerfile if the service is not a placeholder
                        if 'placeholder-' not in service['name']:
                            container_suffix = ''
                            if add_container_suffix:
                                container_suffix = container['name']
                            docker_run_script_name = os.path.join(env_path, 'run_' + service['name'] + container_suffix + '.sh')
                            with open(docker_run_script_name, 'w') as writer:
                                writer.write('docker run --name ' + service['name'] + ' \\\n')
                                writer.write('  --restart always -dit \\\n')

                                # add port mappings
                                if 'port_mappings' in container:
                                    for port_mapping in container['port_mappings']:
                                        # if source not specified, assume it's same as target
                                        port_mapping_source = str(port_mapping['target'])
                                        if 'source' in port_mapping:
                                            port_mapping_source = str(port_mapping['source'])
                                        writer.write('  -p ' + str(port_mapping['target']) + ':' + port_mapping_source + ' \\\n')

                                # gpu-enabled
                                if 'gpus' in container and not (non_gpu_environment in environment['name'].lower() and service['name'] in services_requiring_gpu):
                                    writer.write('  --gpus ' + str(container['gpus']) + ' \\\n')

                                # write universal environment variables
                                writer.write('  -e '  + self.prefix + '_ENVIRONMENT_NAME=' + environment['name'] + ' \\\n')
                                writer.write('  -e '  + self.prefix + '_SERVICE_NAME=' + service['name'] + ' \\\n')
                                if 'port_mappings' in container:
                                    writer.write('  -e '  + self.prefix + '_SERVICE_MAIN_PORT=' + str(container['port_mappings'][0]['target']) + ' \\\n')

                                # add container environment variables that apply across all environments
                                if 'environment_variables' in container:
                                    for environment_variable in container['environment_variables']:
                                        if 'value' in environment_variable and 'placeholder' not in environment_variable['name'].lower():
                                            if environment_variable['name'] == 'USE_GPU' and non_gpu_environment in environment['name'].lower() and service['name'] in services_requiring_gpu:
                                                use_gpu_found = True
                                            else:    
                                                writer.write('  -e ' + environment_variable['name'] + '=' + str(environment_variable['value']) + ' \\\n')
                                        # if proxy port, populate with proxy value
                                        elif 'PROXY_PORT' in environment_variable['name']:
                                            if environment_variable['name'] in proxy_ports:
                                                writer.write('  -e ' + environment_variable['name'] + '=' + str(proxy_ports[environment_variable['name']]) + ' \\\n')
                                        # use default value for hosts, they tend to be the same for single-server deployments
                                        elif 'host' in environment_variable['name'].lower() and 'default_host' in environment and 'placeholder' not in environment_variable['name'].lower():
                                            # check if environment-specific value exists
                                            env_specific_value_exists = False
                                            if 'environment_variables' in environment:
                                                for env_var in environment['environment_variables']:
                                                    if env_var['name'] == environment_variable['name']:
                                                        env_specific_value_exists = True
                                            if not env_specific_value_exists:
                                                writer.write('  -e ' + environment_variable['name'] + '=' + environment['default_host'] + ' \\\n')
                                        # try to populate service port
                                        elif 'SERVICE_PORT' in environment_variable['name']:
                                            service_name = environment_variable['name'].replace('_SERVICE_SERVICE_PORT','').replace('_','-').lower()
                                            for service_find_port in self.yml[self.sdi]['services']:
                                                if service_name == service_find_port['name'] and 'port_mappings' in service_find_port['containers'][0]:
                                                    writer.write('  -e ' + environment_variable['name'] + '=' + str(service_find_port['containers'][0]['port_mappings'][0]['target']) + ' \\\n')

                                # add environment-specific container environment variables
                                #   .. which can include overrides of general variables 
                                if 'environment_variables' in environment:
                                    for environment_variable in environment['environment_variables']:
                                        if 'environment_variables' in container:
                                            for env_var_container in container['environment_variables']:
                                                if env_var_container['name'] == environment_variable['name']:
                                                    writer.write('  -e ' + environment_variable['name'] + '=' + str(environment_variable['value']) + ' \\\n')
                                        # add universal environment variables
                                        if 'universal' in environment_variable:
                                            writer.write('  -e ' + environment_variable['name'] + '=' + str(environment_variable['value']) + ' \\\n')

                                # update env vars for haproxy
                                if 'haproxy' in service['name']:
                                    for port_mapping in container['port_mappings']:
                                        if 'name' in port_mapping:
                                            writer.write('  -e ' + port_mapping['name'] + '=' + str(port_mapping['target']) + ' \\\n')

                                # check if elasticsearch container, and also memory limits
                                # these two are bunched together as memory limits are critical for elasticsearch
                                write_memory_limit = False
                                default_es_memory_limit = '3G'
                                if 'elasticsearch' in container['name']:
                                    write_memory_limit = True
                                # try to load memory limit, else use default
                                try:
                                    es_memory_limit = container['resources']['limits']['memory']
                                    write_memory_limit = True
                                except:
                                    es_memory_limit = default_es_memory_limit
                                if write_memory_limit:
                                    writer.write('  -m ' + es_memory_limit + ' \\\n')

                                # add volume mappings
                                if 'volume_mappings' in environment:
                                    for vol_service in environment['volume_mappings']:
                                        if vol_service['service_name'] == service['name']:
                                            writer.write('  -v ' + vol_service['source'] + ':' + vol_service['target'] + ' \\\n')

                                # add entrypoint:
                                if 'entrypoint' in container:
                                    writer.write('  --entrypoint ' + container['entrypoint'] + ' \\\n')

                                if container['name'] not in images_to_pull:
                                    images_to_pull.append(container['name'])
                                    if 'omit_image_registry_for_non_internet_environments' in container and 'soe' not in environment['name'].lower():
                                        pli.write('docker pull ' + container['name'] + '\n')
                                    else:
                                        pli.write('docker pull ' + os.path.join(environment['image_registry'], container['name'].split('/')[-1]) + '\n')
                                if 'omit_image_registry_for_non_internet_environments' in container and 'soe' not in environment['name'].lower():
                                    writer.write('  ' + container['name'])
                                else:
                                    writer.write('  ' + os.path.join(environment['image_registry'], container['name'].split('/')[-1]))

                            # make docker run script executable
                            f = Path(docker_run_script_name)
                            f.chmod(f.stat().st_mode | stat.S_IEXEC)

                            # write k8s deployment file
                            k8s_file_has_command = False
                            k8s_deployment_file_name = os.path.join(env_path, 'k8s-' + service['name']+'.yaml')
                            # get k8s template
                            if 'ingress_path' in service:
                                resource_path = '/'.join(('templates', 'k8s_template_ingress.yaml'))  
                                f = pkg_resources.resource_stream(resource_package, resource_path)
                                # f = open("templates/k8s_template_ingress.yaml", "r")
                            else:
                                # f = open("templates/k8s_template.yaml", "r")
                                resource_path = '/'.join(('templates', 'k8s_template.yaml'))  
                                f = pkg_resources.resource_stream(resource_package, resource_path)
                            k8s_yml = list(yaml.load_all(f))
                            f.close()
                            # figure out which config is which

                            for num, name in enumerate(k8s_yml, start=0):
                                if name['kind'] == 'Deployment':
                                    self.ddi = num
                                elif name['kind'] == 'Service':
                                    self.sdi = num
                                elif name['kind'] == 'Ingress':
                                    self.idi = num

                            # populate deployment params
                            k8s_deployment_details = k8s_yml[self.ddi]
                            k8s_deployment_details['metadata']['name'] = service['name']
                            k8s_deployment_details['metadata']['labels']['app'] = service['name']
                            k8s_deployment_details['spec']['selector']['matchLabels']['app'] = service['name']
                            k8s_deployment_details['spec']['template']['metadata']['labels']['app'] = service['name']

                            if 'replicas' in service:
                                k8s_deployment_details['spec']['replicas'] = service['replicas']

                            for idx, container in enumerate(service['containers'], start=0):
                                counter = 0
                                if idx == 0:
                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['name'] = service['name']
                                else:
                                    k8s_deployment_details['spec']['template']['spec']['containers'].append({'name':service['name']})
                                if 'omit_image_registry_for_non_internet_environments' in container and 'soe' not in environment['name'].lower():
                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['image'] = service['containers'][0]['name']
                                else:
                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['image'] = os.path.join(environment['image_registry'], service['containers'][0]['name'].split('/')[-1])

                                # add stdin and tty to container, equivalent of -it in docker
                                k8s_deployment_details['spec']['template']['spec']['containers'][idx]['stdin'] = True
                                k8s_deployment_details['spec']['template']['spec']['containers'][idx]['tty'] = True

                                # add universal environment variables
                                k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'] = []
                                k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'].append({'name':self.prefix + '_ENVIRONMENT_NAME'})
                                k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'][counter]['value'] = environment['name']   
                                counter += 1
                                k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'].append({'name':self.prefix + '_SERVICE_NAME'})
                                k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'][counter]['value'] = service['name']
                                counter += 1
                                if 'port_mappings' in container:
                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'].append({'name':self.prefix + '_SERVICE_MAIN_PORT'})
                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'][counter]['value'] = container['port_mappings'][0]['target']
                                    counter += 1

                                # add container environment variables that apply across all environments
                                if 'environment_variables' in container:
                                    for environment_variable in container['environment_variables']:
                                        if 'value' in environment_variable:
                                            if environment_variable['name'] == 'USE_GPU' and 'soe' in environment['name'].lower() and service['name'] in services_requiring_gpu:
                                                use_gpu_found = True
                                            else:    
                                                k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'].append({'name':environment_variable['name']})
                                                k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'][counter]['value'] = str(environment_variable['value'])
                                                counter += 1

                                        # use default value for hosts, they tend to be the same for single-server deployments
                                        # for k8s, ignore env vars ending with SERVICE_HOST, as those are managed by k8s separately
                                        elif 'host' in environment_variable['name'].lower() and 'default_host' in environment and not environment_variable['name'].endswith('SERVICE_HOST') and not environment_variable['name'].endswith('SERVICE_PORT'):
                                            # check if environment-specific value exists
                                            env_specific_value_exists = False
                                            if 'environment_variables' in environment:
                                                for env_var in environment['environment_variables']:
                                                    if env_var['name'] == environment_variable['name']:
                                                        env_specific_value_exists = True
                                            if not env_specific_value_exists:
                                                k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'].append({'name':environment_variable['name']})
                                                k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'][counter]['value'] = str(environment['default_host'])
                                                counter += 1

                                        # if env var is service port, scan service ports
                                        if 'SERVICE_PORT' in environment_variable['name'] and 'include_in_k8' in environment_variable:
                                            service_name = environment_variable['name'].replace('_SERVICE_SERVICE_PORT','').replace('_','-').lower()
                                            for service_find_port in self.yml[self.sdi]['services']:
                                                if service_name == service_find_port['name'] and 'port_mappings' in service_find_port['containers'][0]:
                                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'].append({'name':environment_variable['name']})
                                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'][counter]['value'] = str(service_find_port['containers'][0]['port_mappings'][0]['target'])
                                                    counter += 1 

                                # add environment-specific container environment variables
                                #   .. which can include overrides of general variables 
                                if 'environment_variables' in environment:
                                    for environment_variable in environment['environment_variables']:
                                        if 'environment_variables' in container:
                                            for env_var_container in container['environment_variables']:
                                                # for k8s, ignore env vars ending with SERVICE_HOST, as those are managed by k8s separately
                                                if env_var_container['name'] == environment_variable['name'] and not environment_variable['name'].endswith('SERVICE_HOST') and 'placeholder' not in environment_variable['name']:
                                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'].append({'name':environment_variable['name']})
                                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'][counter]['value'] = str(environment_variable['value'])
                                                    counter += 1
                                                # however, include the env vars where the flag include_in_k8 is present, e.g. databases and other external dependencies
                                                if env_var_container['name'] == environment_variable['name'] and 'include_in_k8' in env_var_container:
                                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'].append({'name':environment_variable['name']})
                                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'][counter]['value'] = str(environment_variable['value'])
                                                    counter += 1     

                                        # add universal environment variables
                                        if 'universal' in environment_variable:
                                            k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'].append({'name':environment_variable['name']})
                                            k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'][counter]['value'] = str(environment_variable['value'])
                                            counter += 1

                                # update env vars for haproxy
                                if 'haproxy' in service['name']:
                                    for port_mapping in container['port_mappings']:
                                        if 'name' in port_mapping:
                                            k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'].append({'name':port_mapping['name']})
                                            k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env'][counter]['value'] = str(port_mapping['target'])
                                            counter += 1

                                # delete env key if no environment variables
                                if 'env' in k8s_deployment_details['spec']['template']['spec']['containers'][idx]:
                                    if len(k8s_deployment_details['spec']['template']['spec']['containers'][idx]['env']) == 0:
                                        k8s_deployment_details['spec']['template']['spec']['containers'][idx].pop('env', None)

                                # check if elasticsearch container, and also memory limits
                                # these two are bunched together as memory limits are critical for elasticsearch
                                write_memory_limit = False
                                if 'elasticsearch' in container['name']:
                                    write_memory_limit = True
                                    # default_es_memory_limit was defined earlier in docker section
                                # try to load memory limit, else use default
                                try:
                                    es_memory_limit = container['resources']['limits']['memory']
                                    write_memory_limit = True
                                except:
                                    es_memory_limit = default_es_memory_limit
                                if write_memory_limit:
                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['resources'] = {'limits':{'memory':es_memory_limit}}

                                # add entrypoint, if present
                                if 'entrypoint' in container:
                                    # k8s command format is in list format
                                    # having problems appending array in ['item'] format, using regex to fix for now
                                    k8s_file_has_command = True
                                    args = []
                                    for idx_cmd, term in enumerate(container['entrypoint'].split(), start=0):
                                        if idx_cmd == 0:
                                            k8s_deployment_details['spec']['template']['spec']['containers'][idx]['command'] = str([term])
                                        else:
                                            args.append(term)
                                    if len(args)>0:
                                        k8s_deployment_details['spec']['template']['spec']['containers'][idx]['args'] = str(args)

                                # add livenessProbe if present
                                if 'livenessProbe' in container:
                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['livenessProbe'] = container['livenessProbe']

                                # add GPU if present
                                if 'gpus' in container:
                                    k8s_deployment_details['spec']['template']['spec']['containers'][idx]['limits'] = {'nvidia.com/gpu':container['gpus']}

                            # add initContainer if present
                            if 'initContainers' in service:
                                k8s_deployment_details['spec']['template']['spec']['initContainers'] = service['initContainers']

                            # update volume mappings
                            if 'volume_mappings' in environment:
                                for vol_service in environment['volume_mappings']:
                                    if vol_service['service_name'] == service['name']:
                                        k8s_deployment_details['spec']['template']['spec']['volumes'] = [{'name':service['name'] + '-pv-storage'}]
                                        k8s_deployment_details['spec']['template']['spec']['volumes'][0]['persistentVolumeClaim'] = {'claimName':service['name'] + '-pv-claim'}
                                        k8s_deployment_details['spec']['template']['spec']['containers'][idx]['volumeMounts'] = [{'mountPath':vol_service['target']}]
                                        k8s_deployment_details['spec']['template']['spec']['containers'][idx]['volumeMounts'][0]['name'] = service['name'] + '-pv-storage'
                                        if 'soe' in environment['name'].lower():
                                            resource_path = '/'.join(('templates', 'k8s_template_volume_claim_tanzu.yaml'))  
                                            f = pkg_resources.resource_stream(resource_package, resource_path)
                                            # f = open("templates/k8s_template_volume_claim_tanzu.yaml", "r")
                                        else:
                                            resource_path = '/'.join(('templates', 'k8s_template_volume_claim.yaml'))  
                                            f = pkg_resources.resource_stream(resource_package, resource_path)
                                            # f = open("templates/k8s_template_volume_claim.yaml", "r")
                                        k8s_volume_claim = list(yaml.load_all(f))[0]
                                        f.close()
                                        k8s_volume_claim['metadata']['name'] = service['name'] + '-pv-claim'
                                        if 'size' in vol_service:
                                            k8s_volume_claim['spec']['resources']['requests']['storage'] = vol_service['size']
                                        k8s_volume_claim_file_name = os.path.join(env_path, 'k8s-' + service['name']+'-pv-claim.yaml')
                                        with open(k8s_volume_claim_file_name, 'w') as file:
                                            yaml.dump(k8s_volume_claim, file)

                            # populate service params
                            k8s_service_details = k8s_yml[self.sdi]
                            k8s_service_details['metadata']['name'] = service['name'] + '-service'
                            k8s_service_details['spec']['selector']['app'] = service['name'] 
                            # add port mappings
                            if 'port_mappings' in container:
                                # if service has > 1 port, k8s needs each port to be named
                                add_port_names = False
                                if len(container['port_mappings']) > 1:
                                    add_port_names = True
                                for idx, port_mapping in enumerate(container['port_mappings'], start=0):
                                # if source not specified, assume it's same as target
                                    k8s_service_details['spec']
                                    if idx == 0:
                                        k8s_service_details['spec']['ports'][idx]['port'] = port_mapping['target']
                                    else:
                                        k8s_service_details['spec']['ports'].append({'port':port_mapping['target']})
                                    if 'source' in port_mapping:
                                        k8s_service_details['spec']['ports'][idx]['targetPort'] = port_mapping['source']
                                    if add_port_names:
                                        k8s_service_details['spec']['ports'][idx]['name'] = 'port' + str(idx)

                            # update ingress details, if present
                            if 'ingress_path' in service and 'port_mappings' in container:
                                k8s_ingress_details = k8s_yml[self.idi]
                                k8s_ingress_details['metadata']['name'] = service['name'] + '-ingress'
                                k8s_ingress_details['spec']['rules'][0]['http']['paths'][0]['path'] = service['ingress_path']
                                k8s_ingress_details['spec']['rules'][0]['http']['paths'][0]['backend']['service']['name'] = service['name'] + '-service'
                                k8s_ingress_details['spec']['rules'][0]['http']['paths'][0]['backend']['service']['port']['number'] = container['port_mappings'][0]['target']

                            with open(k8s_deployment_file_name, 'w') as file:
                                yaml.dump_all(k8s_yml, file)

                            # k8s is quite finicky on some formatting for its files
                            # need to reopen the file and adjust for command list, numeric env vars and other things
                            k8s_file = open(k8s_deployment_file_name)
                            k8s_file_content = k8s_file.read()
                            k8s_file.close()
                            k8s_file_content = re.sub(r'command: \"([^\"]*)\"', r'command: \1', k8s_file_content)
                            k8s_file_content = re.sub(r'args: \"([^\"]*)\"', r'args: \1', k8s_file_content)
                            k8s_file_content = re.sub(r'value: ([0-9][^\n]*)', r'value: "\1"', k8s_file_content)
                            k8s_file = open(k8s_deployment_file_name, 'w')
                            k8s_file.write(k8s_file_content)
                            k8s_file.close()

            # write convenience script to pull images
            pli.close()
            # make script executable
            f = Path(pull_latest_images_script_name)
            f.chmod(f.stat().st_mode | stat.S_IEXEC)

            # update monitoring settings
            if update_monitoring:
                monitoring_settings = {}
                for service in self.yml[self.sdi]['services']:
                    if service['name'] == self.prefix.lower() + '-util-monitoring':
                        monitoring_settings['refresh_interval_in_seconds'] = service['refresh_interval_in_seconds']
                        non_api_services_to_monitor = {}
                        if 'non_api_services_to_monitor' in service:
                            for non_api_service in service['non_api_services_to_monitor']:
                                non_api_services_to_monitor[non_api_service['name']] = non_api_service['threshold_in_minutes']
                            monitoring_settings['non_api_services_to_monitor'] = non_api_services_to_monitor
                        api_services_to_monitor = {}
                        if 'api_services_to_monitor' in service:
                            for api_service in service['api_services_to_monitor']:
                                api_services_to_monitor[api_service['name']] = api_service['endpoint']
                            monitoring_settings['api_services_to_monitor'] = api_services_to_monitor  
                self.redis_object.set(self.prefix.lower() + '-monitoring-settings', json.dumps((monitoring_settings)))

    def generate_architecture_diagram(self):
        show_ports = False
        show_data_flows = True
        categories = [{'name':'Default', 'services':[]}]
        architecture = {}

        # function to clean service name for display in diagram
        # some names are too long and not necc in diagram
        def clean_service_name(service_name):
            return service_name.replace(self.prefix.lower() + '-','').replace('placeholder-','').replace('external-structured-','').replace('app-','')

        # nested function to populate service categories
        def populate_category(current_category, service_category, service_name):
            if 'architecture_categories' in service_category:
                # nested category
                next_category = service_category['architecture_categories'][0]
                if 'categories' not in current_category:
                    current_category['categories'] = []
                category_found = False
                for cat in current_category['categories']:
                    if cat['name'] == next_category['name']:
                        category_found = True
                if not category_found:
                    current_category['categories'].append({'name':next_category['name']})   

                for category in current_category['categories']:
                    category = populate_category(category, next_category, service_name)
            else:
                # non-nested category, add service name
                if 'services' not in current_category:
                    current_category['services'] = []
                if current_category['name'] == service_category['name']:
                    current_category['services'].append({'name':service_name})
            return current_category

        # nested function to populate diagram clusters
        def populate_clusters(architecture, categories):
            for category in categories:
                with Cluster(category['name']):
                    for service in category['services']:
                        service_name = service['name']
                        if 'kafka' in service_name.lower():
                            architecture[service_name] = Kafka(clean_service_name(service_name))
                        elif '-db' in service_name.lower():
                            architecture[service_name] = Database(clean_service_name(service_name))
                        elif '-es-' in service_name.lower():
                            architecture[service_name] = Elasticsearch(clean_service_name(service_name))
                        elif 'logs-monitoring' in service_name.lower():
                            architecture[service_name] = Logstash(clean_service_name(service_name))
                        elif 'ui-view' in service_name.lower():
                            architecture[service_name] = React(clean_service_name(service_name))
                        elif 'react' in service_name.lower():
                            architecture[service_name] = React(clean_service_name(service_name))
                        elif 'app-' in service_name.lower():
                            architecture[service_name] = Django(clean_service_name(service_name))
                        elif 'web-server' in service_name.lower():
                            architecture[service_name] = Nginx(clean_service_name(service_name))
                        elif 'placeholder-email' in service_name.lower():
                            architecture[service_name] = SimpleEmailServiceSesEmail(clean_service_name(service_name))
                        elif 'placeholder-dms' in service_name.lower():
                            architecture[service_name] = MultipleDocuments(clean_service_name(service_name))
                        elif 'placeholder-api' in service_name.lower() or 'placeholder-eden' in service_name.lower():
                            architecture[service_name] = Server(clean_service_name(service_name))
                        elif 'placeholder-user' in service_name.lower():
                            architecture[service_name] = Users(clean_service_name(service_name))
                        elif 'placeholder-internet' in service_name.lower() or 'website' in service_name.lower():
                            architecture[service_name] = Internet(clean_service_name(service_name))
                        elif 'redis' in service_name.lower():
                            architecture[service_name] = Redis(clean_service_name(service_name))
                        elif 'haproxy' in service_name.lower():
                            architecture[service_name] = Haproxy(clean_service_name(service_name))
                        elif 'chatbot-main' in service_name.lower():
                            architecture[service_name] = Custom(clean_service_name(service_name), "chatmascot.png")
                        else:
                            architecture[service_name] = Docker(clean_service_name(service_name))  
                    if 'categories' in category:
                        architecture = populate_clusters(architecture, category['categories'])
            return architecture


        with Diagram(self.prefix + " Architecture", show=True, direction="TB") as diag:
            # need three loops
            # first loop is to gather the categories each service is in.
            for service in self.yml[self.sdi]['services']:
                # get first-level services
                if 'architecture_categories' in service:
                    category_exists = False
                    for category in categories:
                        if category['name'] == service['architecture_categories'][0]['name']:
                            category_exists = True
                    if not category_exists:
                        categories.append({'name':service['architecture_categories'][0]['name'], 'services':[]})
            for service in self.yml[self.sdi]['services']:
                if 'architecture_categories' in service:
                    for idx, category in enumerate(categories):
                        if category['name'] == service['architecture_categories'][0]['name']:
                            if 'architecture_categories' in service['architecture_categories'][0]:
                                category = populate_category(category, service['architecture_categories'][0], service['name'])
                            else:
                                categories[idx]['services'].append({'name':service['name']})
                else:
                    categories[0]['services'].append({'name':service['name']})

            # second loop is to set out each cluster (included nested ones), and nodes inside
            architecture = populate_clusters(architecture, categories)

            # third loop is to use service linkages via environment variables to connect the services
            for service in self.yml[self.sdi]['services']:
                for container in service['containers']:
                    if 'environment_variables' in container:
                        for environment_variable in container['environment_variables']:
                            service_name = environment_variable['name'].replace('_SERVICE_SERVICE_HOST','').replace('_','-').lower()
                            if service_name in architecture:
                                idx = 0
                                port_exists = False
                                for i, svc in enumerate(self.yml[self.sdi]['services'], start=0):
                                    if svc['name'] == service_name and 'port_mappings' in svc['containers'][0]:
                                        idx = i
                                        port_exists = True
                                if port_exists and show_ports:
                                    architecture[service['name']] - Edge(label=str(self.yml[self.sdi]['services'][idx]['containers'][0]['port_mappings'][0]['target'])) - architecture[service_name]
                                else:
                                    architecture[service['name']] - Edge() - architecture[service_name]

                                # TODO add data flows
