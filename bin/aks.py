#!/usr/bin/env python3
from azure.cli.core import get_default_cli
import datetime
import subprocess
import sys
import os
import json
import yaml
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import logging
import csv
import uuid

DIR_NAME = os.path.dirname(os.path.realpath(__file__))
DIR_PARENT = os.path.abspath(os.path.join(DIR_NAME, os.pardir))
RUNID = ""

with open(os.path.join(DIR_PARENT, 'config.yml'), 'r') as config_file:
    config = yaml.load(config_file.read())
LOGIN = [
    'login',
    '--service-principal',
    '--username', config['aks']['user'],
    '--password', config['aks']['password'],
    '--tenant', config['aks']['tenant']
]

def requests_retry_session(retries=3, backoff_factor=0.3, status_forcelist=(500, 502, 504), session=None):
    session = session or requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def setup_custom_logger(name):
    formatter = logging.Formatter(fmt='%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handler = logging.FileHandler(os.path.join(DIR_PARENT, 'log', config['aks']['location']+'-aks.txt'), mode='a')
    handler.setFormatter(formatter)
    screen_handler = logging.StreamHandler(stream=sys.stdout)
    screen_handler.setFormatter(formatter)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(screen_handler)
    return logger

logger = setup_custom_logger('dolos')

def az(command):
    return get_default_cli().invoke(command)

def log(message):
    logger.info(message)

def csvlog(category,timetaken):
    with open(os.path.join(DIR_PARENT, 'log', 'aks.csv'), 'a') as csvFile:
      writer = csv.writer(csvFile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
      writer.writerow([RUNID, config['aks']['location'], datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), category, timetaken])

def status(code):
    if code != 0:
        log("Error")
        return False
    return True

def cleanup():
    global RUNID
    RUNID = uuid.uuid4().hex[:4] + "i" # append a character to force excel to treat column as text
    log("Current run id: %s" % RUNID)

    log("Attempting to delete cluster")
    destroy_start_time = datetime.datetime.now()
    az(['group', 'delete', '--name', 'dolos', '--yes'])
    destroy_end_time = datetime.datetime.now()
    log("Cluster delete finished")
    destroy_total_time = destroy_end_time - destroy_start_time
    log("Total destroy time taken: %s" % str(destroy_total_time))
    #csvlog("DELETE",str(destroy_total_time))
    try:
        subprocess.check_output(['kubectl', 'config', 'unset', 'current-context'])
        subprocess.check_output(['kubectl', 'config', 'unset', 'users.clusterUser_dolos_dolos'])
        subprocess.check_output(['kubectl', 'config', 'delete-cluster', 'dolos'])
        subprocess.check_output(['kubectl', 'config', 'delete-context', 'dolos'])
    except:
        pass

if __name__ == '__main__':

    try:
        while True:

            get_default_cli().invoke(LOGIN)

            cleanup()

            create_start_time = datetime.datetime.now()
            aks_create_start_time = create_start_time
            log("Starting test")

            log("Creating Resource Group")
            group_create = az(['group', 'create', '--name', 'dolos', '--location', config['aks']['location']])
            if not status(group_create):
                break

            log("Creating the AKS cluster")
            aks_create = az(['aks', 'create', '--service-principal', config['aks']['user'], '--client-secret', config['aks']['password'], '--location', config['aks']['location'], '--resource-group', 'dolos', '--name', 'dolos', '--node-count', '1', '--kubernetes-version', '1.11.5', '--generate-ssh-keys'])
            if not status(aks_create):
                break

            aks_create_end_time = datetime.datetime.now()
            aks_create_total_time = aks_create_end_time - aks_create_start_time
            log("az aks create time taken: %s" % str(aks_create_total_time))
            csvlog("CREATE",str(aks_create_total_time))

            log("Getting cluster credentials")
            get_creds = az(['aks', 'get-credentials', '--resource-group', 'dolos', '--name', 'dolos'])
            if not status(get_creds):
                break

            log("Get Nodes")
            nodes = subprocess.check_output(['kubectl', 'get', 'nodes'])
            log(nodes)

            deployment_service_create_start_time = datetime.datetime.now()
            log("Applying Deployment")
            deploy_file = os.path.join(DIR_NAME, 'fixtures', 'azure-vote.yaml')
            deployment = subprocess.check_output(['kubectl', 'apply', '-f', deploy_file])
            log(deployment)

            log("Getting external IP")
            while True:
                try:
                    service = subprocess.check_output(['kubectl', 'get', 'service', 'azure-vote-front', '--output', 'json'])
                    external_ip = json.loads(service)['status']['loadBalancer']['ingress'][0]['ip']
                    if external_ip:
                        if any(char.isdigit() for char in external_ip):
                            break
                except:
                    pass

            deployment_service_create_end_time = datetime.datetime.now()
            deployment_service_create_total_time = deployment_service_create_end_time - deployment_service_create_start_time
            log("ip available: %s" % str(deployment_service_create_total_time))
            csvlog("SERVICE_PUBLIC_IP",str(deployment_service_create_total_time))

            get_content_start_time = datetime.datetime.now()

            log("Getting web contents from %s" % external_ip)

            content = ""
            while content == "":
                try:
                    url = 'http://' + str(external_ip)
                    response = requests_retry_session().get(url)
                    content = response.text
                except Exception as x:
                    log("web content failed: %s" % x.__class__.__name__)


            with open(os.path.join(DIR_NAME, 'fixtures', 'azure-vote.html'), 'r') as testfile:
                test_data = testfile.read()

            if test_data == content:
                log("Test passed. Cluster complete.")
                create_end_time = datetime.datetime.now()
                create_total_time = create_end_time - create_start_time
                get_content_end_time = create_end_time - get_content_start_time
                log("Total create time taken: %s" % str(create_total_time))
                csvlog("WEBLOAD",str(get_content_end_time))
            else:
                log("Test failed.")

    except KeyboardInterrupt:
        log('interrupted!')
