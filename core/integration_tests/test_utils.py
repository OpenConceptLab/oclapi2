import json
import os

from jsonpath_ng import parse


def load_json(file, parent_dir=None):
    module_dir = os.path.dirname(__file__)  # get current directory
    if parent_dir:
        file_path = os.path.join(module_dir, parent_dir)
    file_path = os.path.join(file_path, file)
    with open(file_path) as f:
        json_file = json.load(f)
    return json_file


def update_json(json, path, value):
    jsonpath_expr = parse(path)
    jsonpath_expr.update_or_create(json, value)


def find(json, path):
    jsonpath_expr = parse(path)
    return jsonpath_expr.find(json)


def ignore_json_paths(self, json_input, json_response, paths):
    for path in paths:
        self.update(json_input, path, None)
        self.update(json_response, path, None)

