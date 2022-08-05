# content of conftest.py
from datetime import datetime, date, timezone
import time
import random
from wsgiref import headers
import pytest
import logging
# We need a yaml parser, e.g. PyYAML.
import yaml
from jinja2 import Environment, PackageLoader, select_autoescape
from faker import Faker
import socket
import requests

Faker.seed(random.randint(0,65000))
fake = Faker()

logging.basicConfig(level=logging.DEBUG)
mylogger = logging.getLogger()

def pytest_collect_file(parent, file_path):
    mylogger.debug(f"Parent={parent} file_path={file_path}")
    if file_path.suffix == ".yaml" and file_path.name.startswith("test"):
        return YamlFile.from_parent(parent, path=file_path)


class YamlFile(pytest.File):
    def collect(self):

        raw = yaml.safe_load(self.path.open())
        for name, spec in sorted(raw.items()):
            yield YamlItem.from_parent(self, name=name, spec=spec)


class YamlItem(pytest.Item):
    def __init__(self, *, spec, **kwargs):
        super().__init__(**kwargs)
        self.spec = spec

    def runtest(self):
        # logging.debug(self.spec.items())
        jenv = Environment()
        
        assert self.spec['metadata']['vendor'] is not None, "metadata.vendor is required" 
        assert self.spec['metadata']['product'] is not None, "metadata.product is required" 
        assert self.spec['metadata']['version'] is not None, "metadata.version is required" 


        jinjaArgs = {}
        tsArgs = {}
        for eventfield in self.spec['events']['fields']:
            mylogger.debug(eventfield)

            eventFieldName = eventfield['token']
            mylogger.debug(f"working on field={eventFieldName}")
            generator = eventfield['generator']['class']
            mylogger.debug(f"working on generator={generator}")
            if generator == "strftime":
                if 'timezone' in eventfield['generator'] and eventfield['generator']['timezone'] == "UTC":
                    dt = datetime.now(timezone.utc)
                else: 
                    dt = datetime.now()
                jinjaArgs[eventFieldName]=dt.strftime(eventfield['generator']['format'])
                tsArgs[eventFieldName]=dt
            elif generator == "host":
                generatorField=eventfield['generator']['field']
                if generatorField == "fqdn":
                    jinjaArgs[eventFieldName]=fake.hostname()
                elif generatorField == "long":
                    jinjaArgs[eventFieldName]=fake.hostname(2)
                else:
                    jinjaArgs[eventFieldName]=fake.hostname(0)
            elif generator == "user":
                generatorField=eventfield['generator']['field']
                if generatorField == "short":
                    jinjaArgs[eventFieldName]=fake.user_name()
                elif generatorField == "email":
                    jinjaArgs[eventFieldName]=fake.ascii_safe_email()
            elif generator == "network":
                generatorField=eventfield['generator']['field']
                if generatorField == "ipv4":
                    jinjaArgs[eventFieldName]=fake.ipv4()
                elif generatorField == "ipv6":
                    jinjaArgs[eventFieldName]=fake.ipv6()
                elif generatorField == "src_port":
                    jinjaArgs[eventFieldName]=str(fake.port_number(is_dynamic=True))
                else:
                    jinjaArgs[eventFieldName]=fake.ipv4()

            elif generator == "syslog":
                generatorField=eventfield['generator']['field']
                mylogger.debug(f"working on generatorField={generatorField}")
                if generatorField == "pri":
                    rPRI = random.randint(2,100)
                    mylogger.debug(f"rPRI={rPRI}")
                    jinjaArgs[eventFieldName]=f'{rPRI}'
            else:
                raise Exception(f"Unknown Generator={generator}")

        mylogger.debug(f"-----tokens={jinjaArgs}")
        mylogger.debug(f"-----tsArgs={tsArgs}")
        
        rawEvents = []
        for jtemplate in self.spec['events']['templates']:
            ctemplate = jenv.from_string(jtemplate)

            rendered = ctemplate.render(jinjaArgs)
            mylogger.debug(f"step1 rendered={rendered}")
            for eventfield in self.spec['events']['fields']:
                eventFieldName = eventfield['token']
                if eventfield['placeholder']['type'] == "literal":
                    orig = eventfield['placeholder']['value']
                    new = jinjaArgs[eventFieldName]
                    mylogger.debug(f"replacing {orig} with {new}")
                    rendered=rendered.replace(orig,new)

            mylogger.debug(f"step2 rendered={rendered}")
            rawEvents.append(rendered)

        for transport in self.spec['events']['transports']:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((transport['host'], int(transport['port'])))
            for raw in rawEvents:
                s.sendall(raw.encode())
                if transport['wrapper'] == "LF":
                    s.sendall('\n'.encode())
        check = self.spec['events']['checks']['humio']
        mylogger.debug(check)
        checktsfield = check['timestamp']['field']
        checktsdelta = check['timestamp']['delta']
        checkargs={}
        workingts= tsArgs[checktsfield].timestamp()
        startts = workingts - 1
        endts = workingts + 1
        mylogger.debug(f"ts={workingts}")
        checkargs['start'] =int(startts*1000)
        checkargs['end'] =int(endts *1000 )
        # checkargs['start'] = 1659706485000 - 1
        # checkargs['end'] = 1659706485000 + 1
        checkargs['queryString'] ="*"

        mylogger.debug(checkargs)
        url = "https://humio.hql.guru/api/v1/repositories/syslog/query"
        authtoken = "scSDCr7ltk0v8dYvrHHGKtZ0~YzMuodZaFiwWYN4cyRmr0w814qO7KelqYCw6rn6yw2qi"
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {authtoken}',
            'Content-Type': 'application/json'
            }
        time.sleep(2)
        result = requests.post(url, json = checkargs,headers=headers)

        assert result.status_code == 200

        queryresult = result.json()

        mylogger.debug(queryresult)

        assert 1 == len(queryresult)

        firstqueryresult = queryresult[0]
        mylogger.debug(firstqueryresult)
        for checkfield in check['fields']:
            cfield = checkfield['field']
            assert cfield in firstqueryresult, f"Field {cfield} not in result"

            if "value_from" in checkfield:
                assert firstqueryresult[cfield] == jinjaArgs[cfield]
            elif "value" in checkfield:
                assert firstqueryresult[cfield] == checkfield['value']
            elif "template" in checkfield:
                ftemplate = jenv.from_string(checkfield['template'])

                rendered = ftemplate.render(jinjaArgs)
                for eventfield in self.spec['events']['fields']:
                    eventFieldName = eventfield['token']
                    if eventfield['placeholder']['type'] == "literal":
                        orig = eventfield['placeholder']['value']
                        new = jinjaArgs[eventFieldName]
                        mylogger.debug(f"replacing {orig} with {new}")
                        rendered=rendered.replace(orig,new)
                mylogger.debug(f"field rendered={rendered}")

                assert firstqueryresult[cfield] == rendered

        # assert self.spec.items()['metadata']['fail'] is not None, "metadata.fail is required" 

        # for name, value in sorted(self.spec.items()):
        #     # Some custom test execution (dumb example follows).
        #     if name != value:
        #         raise YamlException(self, name, value)

    # def repr_failure(self, excinfo):
    #     """Called when self.runtest() raises an exception."""
    #     if isinstance(excinfo.value, YamlException):
    #         return "\n".join(
    #             [
    #                 "usecase execution failed",
    #                 "   spec failed: {1!r}: {2!r}".format(*excinfo.value.args),
    #                 "   no further details known at this point.",
    #             ]
    #         )

    def reportinfo(self):
        return self.path, 0, f"usecase: {self.name}"


class YamlException(Exception):
    """Custom exception for error reporting."""