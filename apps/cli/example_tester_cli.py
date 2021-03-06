#! /usr/bin/python3

import os
import sys
import datetime
import argparse
import yaml
import types
import random

import dt_db_base
import dt_cli_base
import example_lib

parser = argparse.ArgumentParser(description='Command line interface for Example tester')
parser.add_argument('-v','--verbose', help='Increase log information', action='store_true')
parser.add_argument('--config', help='DB config file to use', type=str)
parser.add_argument('command', help='command followed by arguments.', nargs='*')

def fake_dev(context, cmd_args):
    assert len(cmd_args) == 1, "fake_dev takes one argument, the fake device <serial number>"
    serial_number = cmd_args[0]
    uuid = ("%02u:%02u:%02u:%02u:%02u:%02u" % (*[random.randint(0,255) for i in range(0,6)],))
    dev = example_lib.db_example_dev.create(context.db, serial_number, uuid)

cmds = dt_cli_base.generic_cmds.copy()
cmds["fake_dev"] = (fake_dev, "Make a fake device for debug with <serial number>.")

def main():

    print("Command Line Example Tester", datetime.datetime.utcnow())

    args = vars(parser.parse_args())

    cmd = args['command']
    cmd_args = cmd[1:]
    cmd = cmd[0] if len(cmd) else None

    if cmd is None:
        parser.print_help()
        print("\n")
        dt_cli_base.print_cmd_help(cmds)
        sys.exit(-1)

    if args['config']:
        db_def_file = args['config']
    else:
        db_def_file = "config_sqlite_db.yaml"

    with open(db_def_file) as f:
        db_def_gen = yaml.safe_load_all(f)
        db_def = [root for root in db_def_gen][0]

    db_def['sql'] = example_lib.example_sql_common()
    db_def["fn_get_dev"] = example_lib.db_example_dev.get_by_uuid
    db_def["fn_get_dev_by_sn"] = example_lib.db_example_dev.get_by_serial
    db_def["work_folder"] = os.path.abspath("../gui/files_cache")

    context = example_lib.cli_context_object(args, db_def)

    context.db_init()

    assert context.db, "No database"

    dt_cli_base.execute_cmd(context, cmd, cmd_args, cmds)



if __name__ == "__main__":
    main()
