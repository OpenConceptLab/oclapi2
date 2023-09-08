import json
import os

from jsonpath_ng import parse


def load_json(file, parent_dir=None):
    if parent_dir:
        if parent_dir.startswith(os.path.pathsep):
            file_path = parent_dir
        else:
            module_dir = os.path.dirname(__file__)  # get current directory
            file_path = os.path.join(module_dir, parent_dir)
    else:
        file_path = os.path.dirname(__file__)
    file_path = os.path.join(file_path, file)
    with open(file_path) as f:
        json_file = json.load(f)
    return json_file


def update_json(json_input, path, value):
    jsonpath_expr = parse(path)
    jsonpath_expr.update_or_create(json_input, value)


def find(json_input, path):
    jsonpath_expr = parse(path)
    return jsonpath_expr.find(json_input)


def ignore_json_paths(self, json_input, json_response, paths):
    for path in paths:
        self.update(json_input, path, None)
        self.update(json_response, path, None)
