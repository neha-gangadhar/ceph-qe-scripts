""" test_on_nfs_io.py - Test Creation, Deletion, Move operations on nfs-ganesha

Usage: test_on_nfs_io.py -c <input_yaml> -r config/rgw_user.yaml

<input_yaml>
	Note: Any one of these yamls can be used
    test_on_nfs_io_create.yaml
    test_on_nfs_io_delete.yaml
    test_on_nfs_io_move.yaml

"""
# Test basic NFS IO operations on nfs-ganesha
import os
import sys

sys.path.append(os.path.abspath(os.path.join(__file__, "../../../..")))
import argparse
import logging
import shutil
import time
import traceback

import v2.utils.utils as utils
import yaml
from v2.lib.exceptions import NFSGaneshaMountError, TestExecError
from v2.lib.nfs_ganesha.nfslib import DoIO
from v2.lib.nfs_ganesha.write_io_info import BasicIOInfoStructure, IOInfoInitialize

# from initialize import PrepNFSGanesha
from v2.tests.nfs_ganesha.initialize import PrepNFSGanesha
from v2.tests.nfs_ganesha.verify_on_s3 import ReadIOInfoOnS3
from v2.utils.log import configure_logging
from v2.utils.test_desc import AddTestInfo

SLEEP_TIME = 60
log = logging.getLogger()


def test_exec(rgw_user_info_file, config):
    test_info = AddTestInfo("NFS Basic Ops")
    test_info.started_info()
    log.info("config:\n%s" % config["config"])
    log.info("rgw_user_info_file: %s" % rgw_user_info_file)
    io_config = config["config"]
    io_op_config = io_config["io_op_config"]
    log.info("io_op_config: %s" % io_op_config)
    log.info("initiating nfs ganesha")
    io_info_initialize = IOInfoInitialize()
    basic_io_structure = BasicIOInfoStructure()
    io_info_initialize.initialize(basic_io_structure.initial())

    nfs_ganesha = PrepNFSGanesha(rgw_user_info_file=rgw_user_info_file)
    mounted = nfs_ganesha.initialize()
    if not mounted:
        raise TestExecError("mount failed")
    log.info("authenticating rgw user")
    mount_point = nfs_ganesha.rgw_user_info["nfs_mnt_point"]
    if (
        nfs_ganesha.rgw_user_info["nfs_version"] == 4
        and nfs_ganesha.rgw_user_info["Pseudo"] is not None
    ):
        log.info("nfs version: 4")
        log.info("adding Pseudo path to writable mount point")
        mount_point = os.path.join(mount_point, nfs_ganesha.rgw_user_info["Pseudo"])
        log.info("writable mount point with Pseudo: %s" % mount_point)

    if io_op_config.get("create", None):
        do_io = DoIO(nfs_ganesha.rgw_user_info, mount_point)
        # base dir creation
        for bc in range(io_config["basedir_count"]):
            basedir_name_to_create = utils.gen_bucket_name_from_userid(
                nfs_ganesha.rgw_user_info["user_id"], rand_no=bc
            )
            log.info("creating basedir with name: %s" % basedir_name_to_create)
            do_io.write("basedir", basedir_name_to_create)
            if io_config["subdir_count"] != 0:
                for sd in range(io_config["subdir_count"]):
                    subdir_name_to_create = utils.gen_bucket_name_from_userid(
                        basedir_name_to_create + ".subdir", rand_no=sd
                    )
                    log.info("creating subdir with name: %s" % subdir_name_to_create)
                    do_io.write(
                        "subdir",
                        os.path.join(basedir_name_to_create, subdir_name_to_create),
                    )

            if io_config["file_count"] != 0:
                for fc in range(io_config["file_count"]):
                    file_name_to_create = utils.gen_bucket_name_from_userid(
                        basedir_name_to_create + ".file", rand_no=fc
                    )
                    log.info("creating file with name: %s" % file_name_to_create)
                    file_size = utils.get_file_size(
                        io_config["objects_size_range"]["min"],
                        io_config["objects_size_range"]["max"],
                    )
                    do_io.write(
                        "file",
                        os.path.join(basedir_name_to_create, file_name_to_create),
                        file_size,
                    )

        log.info("verification of IO will start after %s seconds" % SLEEP_TIME)
        time.sleep(SLEEP_TIME)
        log.info("starting IO verification on S3")
        read_io_info_on_s3 = ReadIOInfoOnS3()
        read_io_info_on_s3.yaml_fname = "io_info.yaml"
        read_io_info_on_s3.initialize_verify_io()
        bucket_verify = read_io_info_on_s3.verify_if_bucket_created()

        if not bucket_verify:
            raise TestExecError("Bucket verification Failed")

        log.info("Bucket verified, data intact")
        read_io_info_on_s3.verify_if_objects_created()
        log.info("objects verified, data intact")
        log.info("verification completed, data intact")

        if io_op_config.get("delete", None):
            log.info("performing delete operation")
            # if you delete basedirs, objects and files under them will also be deleted
            basedirs_list = read_io_info_on_s3.buckets
            list(
                [
                    shutil.rmtree(os.path.abspath(os.path.join(mount_point, x)))
                    for x in basedirs_list
                ]
            )
            for basedir in basedirs_list:
                if os.path.exists(os.path.abspath(os.path.join(mount_point, basedir))):
                    raise TestExecError("basedir: %s not deleted" % basedir)
            log.info("basedirs and subdirs deleted")

        if io_op_config.get("move", None):
            for each_file in read_io_info_on_s3.objects:
                if each_file["type"] == "file":
                    log.info("performing move operation on %s" % each_file["name"])
                    current_path = os.path.abspath(
                        os.path.join(
                            mount_point, each_file["bucket"], each_file["name"]
                        )
                    )
                    new_path = os.path.abspath(
                        os.path.join(
                            mount_point,
                            each_file["bucket"],
                            each_file["name"] + ".moved",
                        )
                    )
                    moved = utils.exec_shell_cmd(
                        "sudo mv %s %s" % (current_path, new_path)
                    )
                    if moved is False:
                        raise TestExecError("move failed for :%s" % current_path)
                    each_file["name"] = os.path.basename(new_path)

            log.info("Verification will start after %s seconds" % SLEEP_TIME)
            time.sleep(SLEEP_TIME)
            log.info("starting verification for moved files")
            read_io_info_on_s3.verify_if_objects_created()
            log.info("objects verified after move operation, data intact")

    # cleanup and unmount tasks for both nfs v3 and v4
    if nfs_ganesha.rgw_user_info["cleanup"]:
        utils.exec_shell_cmd("sudo rm -rf %s%s" % (mount_point, "/*"))
    # Todo: There's a need to change the behaviour of exec_shell_cmd() function which returns
    # an empty string as an output on the successful execution of a command.
    if nfs_ganesha.rgw_user_info["do_unmount"]:
        if nfs_ganesha.do_un_mount() != "":
            raise NFSGaneshaMountError("Unmount failed")

    test_info.success_status("test success")


if __name__ == "__main__":
    config = {}

    try:
        test_info = AddTestInfo("nfs ganesha basic IO test and verification on rgw")
        parser = argparse.ArgumentParser(description="NFS-Ganesha-RGW Automation")
        parser.add_argument("-r", dest="rgw_user_info", help="RGW user info")
        parser.add_argument("-c", dest="test_config", help="Test Configuration")
        parser.add_argument(
            "-log_level",
            dest="log_level",
            help="Set Log Level [DEBUG, INFO, WARNING, ERROR, CRITICAL]",
            default="info",
        )
        args = parser.parse_args()
        rgw_user_info_yaml = args.rgw_user_info
        test_config_yaml = args.test_config
        log_f_name = os.path.basename(os.path.splitext(test_config_yaml)[0])
        configure_logging(f_name=log_f_name, set_level=args.log_level.upper())
        with open(test_config_yaml, "r") as f:
            doc = yaml.safe_load(f)
        test_config = doc
        test_exec(rgw_user_info_yaml, test_config)
        sys.exit(0)

    except (TestExecError, Exception) as e:
        log.info(e)
        log.info(traceback.format_exc())
        test_info.failed_status("test failed")
        sys.exit(1)
