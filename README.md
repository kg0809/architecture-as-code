# Architecture as Code

## Standardise and speed up application architecture and deployments

Architecture as Code is a Python package that allows you to standardise and speed up your application architecture design and deployments.

By populating a configuration file (in YAML format) that sets out each microservice in your application, you get 'three things for the price of one' - 1. automatically-generated docker run scripts, 2. automatically-generated Kubernetes deployment files, and 3. an automatically-generated architecture diagram.

Improve communication amongst your product team, make your devops folks happier, and shorten time-to-market!

## Requirements

It requires Python 3.6 or higher, check your Python version first.

It uses [Graphviz](https://www.graphviz.org/) (via [Diagrams](https://diagrams.mingrammer.com/)) to render the diagram, so you first need to install Graphviz before using this package.

## Installation

Using pip:
`pip install architecture-as-code`

## Usage

Create a file called `config_main.yaml`, with the following example of a web application architecture comprising a front-end microservice, middleware microservice, and database:

```
# Environment config
kind: EnvironmentDetails
environments:

  - name: prod
    image_registry: 192.168.1.1:5000
    default_host: 192.168.1.2

---

# Service config
kind: ServiceDetails
services:

  - name: webapp-react-front-end
    containers:
    - name: react
      port_mappings:
      - target: 3000
      environment_variables:
      - name: WEBAPP_DJANGO_SERVICE_SERVICE_HOST
        
  - name: webapp-django
    containers:
    - name: django
      port_mappings:
      - target: 8000
      environment_variables:
      - name: WEBAPP_DB_SERVICE_SERVICE_HOST

  - name: webapp-db
    containers:
    - name: postgres
      port_mappings:
      - target: 5432
```

Next, run the following code in Jupyter / Python.  If you're not using Jupyter, omit the second and last lines.

```
from architecture_as_code import ArchitectureAsCode
from IPython.display import Image
prefix='WebApp'
aac = ArchitectureAsCode(prefix)
aac(config_main_file_name='config_main.yaml')
aac.generate_architecture_diagram()
Image(filename=prefix.lower() + '_architecture.png') 
```

You will then get three types of outputs:

1. Docker run scripts for each microservice will be generated in the `deployment_files/<environment name>` folder.
2. Kubernetes deployment files for each microservice will generated in the same folder above.
3. An architecture diagram will be created in the file name `webapp_architecture.png`