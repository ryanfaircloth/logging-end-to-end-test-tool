# content of conftest.py
from datetime import datetime, date, timezone
from importlib.metadata import metadata
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

Faker.seed(random.randint(0, 65000))
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
            for transport in spec["transports"]:
                for check in spec["checks"]:
                    testname = f"{name}/{transport['plugin']}/{check['backend']}"
                    testinstance = {
                        "metadata": spec["metadata"],
                        "data": spec["data"],
                        "transport": transport,
                        "check": check,
                    }
                    yield YamlItem.from_parent(self, name=testname, spec=testinstance)


class YamlItem(pytest.Item):
    def __init__(self, *, spec, **kwargs):
        super().__init__(**kwargs)
        self.spec = spec
        self.generatedFields = {}
        self.generatedTimeStamps = {}
        self.jenv = Environment()

        self.generateData()

    def generateData(self):

        fields = self.spec["data"]["fields"]

        for eventfield in fields:
            mylogger.debug(eventfield)

            eventFieldName = eventfield["token"]
            mylogger.debug(f"working on field={eventFieldName}")
            generator = eventfield["generator"]["class"]
            mylogger.debug(f"working on generator={generator}")
            if generator == "strftime":
                if (
                    "timezone" in eventfield["generator"]
                    and eventfield["generator"]["timezone"] == "UTC"
                ):
                    dt = datetime.now(timezone.utc)
                else:
                    dt = datetime.now()
                self.generatedFields[eventFieldName] = dt.strftime(
                    eventfield["generator"]["format"]
                )
                self.generatedTimeStamps[eventFieldName] = dt
            elif generator == "host":
                generatorField = eventfield["generator"]["field"]
                if generatorField == "fqdn":
                    self.generatedFields[eventFieldName] = fake.hostname()
                elif generatorField == "long":
                    self.generatedFields[eventFieldName] = fake.hostname(2)
                else:
                    self.generatedFields[eventFieldName] = fake.hostname(0)
            elif generator == "user":
                generatorField = eventfield["generator"]["field"]
                if generatorField == "short":
                    self.generatedFields[eventFieldName] = fake.user_name()
                elif generatorField == "email":
                    self.generatedFields[eventFieldName] = fake.ascii_safe_email()
            elif generator == "network":
                generatorField = eventfield["generator"]["field"]
                if generatorField == "ipv4":
                    self.generatedFields[eventFieldName] = fake.ipv4()
                elif generatorField == "ipv6":
                    self.generatedFields[eventFieldName] = fake.ipv6()
                elif generatorField == "src_port":
                    self.generatedFields[eventFieldName] = str(
                        fake.port_number(is_dynamic=True)
                    )
                else:
                    self.generatedFields[eventFieldName] = fake.ipv4()

            elif generator == "syslog":
                generatorField = eventfield["generator"]["field"]
                mylogger.debug(f"working on generatorField={generatorField}")
                if generatorField == "pri":
                    rPRI = random.randint(2, 100)
                    mylogger.debug(f"rPRI={rPRI}")
                    self.generatedFields[eventFieldName] = f"{rPRI}"
            else:
                raise Exception(f"Unknown Generator={generator}")

        mylogger.debug(f"-----generatedFields={self.generatedFields}")
        mylogger.debug(f"-----generatedTimeStamps={self.generatedTimeStamps}")

    def renderTemplate(self, template):
        ctemplate = self.jenv.from_string(template)

        rendered = ctemplate.render(self.generatedFields)
        mylogger.debug(f"step1 rendered={rendered}")
        for eventfield in self.spec["data"]["fields"]:
            eventFieldName = eventfield["token"]
            if eventfield["placeholder"]["type"] == "literal":
                orig = eventfield["placeholder"]["value"]
                new = self.generatedFields[eventFieldName]
                mylogger.debug(f"replacing {orig} with {new}")
                rendered = rendered.replace(orig, new)

        mylogger.debug(f"step2 rendered={rendered}")

        return rendered

    def runtest(self):
        # logging.debug(self.spec.items())

        assert (
            self.spec["metadata"]["vendor"] is not None
        ), "metadata.vendor is required"
        assert (
            self.spec["metadata"]["product"] is not None
        ), "metadata.product is required"
        assert (
            self.spec["metadata"]["version"] is not None
        ), "metadata.version is required"

        rawEvents = []
        for jtemplate in self.spec["data"]["templates"]:
            rendered = self.renderTemplate(jtemplate)
            rawEvents.append(rendered)

        transport = self.spec["transport"]
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((transport["host"], int(transport["port"])))
        for raw in rawEvents:
            s.sendall(raw.encode())
            if transport["wrapper"] == "LF":
                s.sendall(b"\n")
        s.close()

        check = self.spec["check"]
        mylogger.debug(check)
        checktsfield = check["timestamp"]["field"]
        checktsdelta = check["timestamp"]["delta"]
        checkargs = {}
        workingts = self.generatedTimeStamps[checktsfield].timestamp()
        startts = workingts - 1
        endts = workingts + 1
        mylogger.debug(f"ts={workingts}")
        checkargs["start"] = int(startts * 1000)
        checkargs["end"] = int(endts * 1000)
        # checkargs['start'] = 1659706485000 - 1
        # checkargs['end'] = 1659706485000 + 1
        queryFields = []
        for checkfield in check["fields"]:
            cfield = checkfield["field"]
            if "query" in checkfield and checkfield["query"] == True:
                if "value_from" in checkfield:
                    queryFields.append(
                        f"{cfield}=\"{self.generatedFields[checkfield['value_from']]}\""
                    )
                elif "value" in checkfield:
                    queryFields.append(f"{cfield}=\"{checkfield['value']}\"")

        if len(queryFields) > 0:
            checkargs["queryString"] = " ".join(queryFields)
        else:
            checkargs["queryString"] = "*"

        mylogger.debug(checkargs)
        url = "https://humio.rfaircloth.com/api/v1/repositories/snag/query"
        authtoken = (
            "1xaZjT6YLr6Z7kNPl6Mopuya~jUUiN8K4L03KRh3xGFITj1H8GgYBq2v7kDZmAwqHi43w"
        )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {authtoken}",
            "Content-Type": "application/json",
        }
        time.sleep(2)
        result = requests.post(url, json=checkargs, headers=headers)

        assert result.status_code == 200

        queryresult = result.json()

        mylogger.debug(queryresult)

        assert 1 == len(queryresult)

        firstqueryresult = queryresult[0]
        mylogger.debug(firstqueryresult)

        assert float(firstqueryresult["@ingesttimestamp"]) >= float(
            firstqueryresult["@timestamp"]
        ), "Timestamp is newer than ingest"

        assert (
            float(firstqueryresult["@ingesttimestamp"])
            - float(firstqueryresult["@timestamp"])
            < 10000
        ), "Lag greater than 10s"

        for checkfield in check["fields"]:
            cfield = checkfield["field"]
            assert cfield in firstqueryresult, f"Field {cfield} not in result"

            if "value_from" in checkfield:
                assert (
                    firstqueryresult[cfield]
                    == self.generatedFields[checkfield["value_from"]]
                )
            elif "value" in checkfield:
                assert firstqueryresult[cfield] == checkfield["value"]
            elif "template" in checkfield:
                rendered = self.renderTemplate(checkfield["template"])

                assert firstqueryresult[cfield] == rendered

    def reportinfo(self):
        return self.path, 0, f"usecase: {self.name}"


class YamlException(Exception):
    """Custom exception for error reporting."""
