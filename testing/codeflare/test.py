#!/usr/bin/env python3

import sys, os
import pathlib
import subprocess
import logging
logging.getLogger().setLevel(logging.INFO)
import datetime
import time
import functools
import traceback
import copy

import yaml
import fire

TESTING_THIS_DIR = pathlib.Path(__file__).absolute().parent
TESTING_UTILS_DIR = TESTING_THIS_DIR.parent / "utils"
PSAP_ODS_SECRET_PATH = pathlib.Path(os.environ.get("PSAP_ODS_SECRET_PATH", "/env/PSAP_ODS_SECRET_PATH/not_set"))
LIGHT_PROFILE = "light"

sys.path.append(str(TESTING_THIS_DIR.parent))
from common import env, config, run, visualize

import prepare

initialized = False
def init(ignore_secret_path=False, apply_preset_from_pr_args=True):
    global initialized
    if initialized:
        logging.debug("Already initialized.")
        return
    initialized = True

    env.init()
    config.init(TESTING_THIS_DIR)

    if apply_preset_from_pr_args:
        config.ci_artifacts.apply_preset_from_pr_args()

    if not ignore_secret_path and not PSAP_ODS_SECRET_PATH.exists():
        raise RuntimeError("Path with the secrets (PSAP_ODS_SECRET_PATH={PSAP_ODS_SECRET_PATH}) does not exists.")


    config.ci_artifacts.detect_apply_light_profile(LIGHT_PROFILE)


def entrypoint(ignore_secret_path=False, apply_preset_from_pr_args=True):
    def decorator(fct):
        @functools.wraps(fct)
        def wrapper(*args, **kwargs):
            init(ignore_secret_path, apply_preset_from_pr_args)
            fct(*args, **kwargs)

        return wrapper
    return decorator

# ---

def save_matbench_files(name, cfg):
    with open(env.ARTIFACT_DIR / "settings", "w") as f:
        print(f"mcad_load_test=true", file=f)
        print(f"name={name}", file=f)

    with open(env.ARTIFACT_DIR / "config.yaml", "w") as f:
        yaml.dump(config.ci_artifacts.config, f, indent=4)

    with open(env.ARTIFACT_DIR / "test_case_config.yaml", "w") as f:
        yaml.dump(cfg, f)


def merge(a, b, path=None):
    "updates a with b"
    if path is None: path = []
    for key in b:
        if key in a and isinstance(a[key], dict) and isinstance(b[key], dict):
            merge(a[key], b[key], path + [str(key)])
        else:
            a[key] = b[key]
    return a


def _run_test_multiple_values(name, test_artifact_dir_p):
    failed_tests = []

    next_count = env.next_artifact_index()
    with env.TempArtifactDir(env.ARTIFACT_DIR / f"{next_count:03d}__mcad_load_test_multiple_values"):
        test_artifact_dir_p[0] = env.ARTIFACT_DIR

        for key, values in config.ci_artifacts.get_config("tests.mcad.test_multiple_values.settings").items():
                logging.info(f"Running the test with multiple values: {key} -> {values}")

                for value in values:
                    next_count = env.next_artifact_index()
                    with env.TempArtifactDir(env.ARTIFACT_DIR / f"{next_count:03d}__mcad_load_test_value__{key}={value}"):
                        logging.info(f"Running the test with value: {key} -> {value}")
                        with open(env.ARTIFACT_DIR / "settings.test_override_value", "w") as f:
                            print(f"{key}={value}", file=f)

                        try:
                            failed = _run_test_and_visualize(name, {key: value})
                            if failed:
                                failed_tests.append(f"{name}|{key}={value}")
                        except Exception as e:
                            import bdb
                            if isinstance(e, bdb.BdbQuit):
                                raise

                            failed_tests.append(f"{name}|{key}={value}")
                            if config.ci_artifacts.get_config("tests.mcad.stop_on_error"):
                                logging.info("Error detected, and tests.mcad.stop_on_error is set. Aborting.")
                                raise

    if failed_tests:
        logging.error(f"_run_test_multiple_values: caught exception(s) in [{', '.join(failed_tests)}].")

    return bool(failed_tests)


def _run_test(name, test_artifact_dir_p, test_override_values=None):
    dry_mode = config.ci_artifacts.get_config("tests.mcad.dry_mode")
    capture_prom = config.ci_artifacts.get_config("tests.mcad.capture_prom")
    prepare_nodes = config.ci_artifacts.get_config("tests.mcad.prepare_nodes")

    test_templates = config.ci_artifacts.get_config("tests.mcad.test_templates")

    parents_to_apply = [name]
    cfg = {"templates": []}
    while parents_to_apply:
        template_name = parents_to_apply.pop()
        cfg["templates"].insert(0, template_name)
        logging.info(f"Applying test template {template_name} ...")
        try:
            test_template = test_templates[template_name]
        except KeyError:
            logging.error(f"Test template {template_name} does not exist. Available templates: {', '.join(test_templates.keys())}")
            raise

        cfg = merge(copy.deepcopy(test_template), cfg)
        if "extends" in cfg:
            parents_to_apply += cfg["extends"]
            del cfg["extends"]

    if test_override_values:
        for key, value in test_override_values.items():
            config.set_jsonpath(cfg, key, value)

    logging.info("Test configuration: \n"+yaml.dump(cfg))

    next_count = env.next_artifact_index()
    with env.TempArtifactDir(env.ARTIFACT_DIR / f"{next_count:03d}__prepare"):
        if prepare_nodes:
            prepare.prepare_test_nodes(name, cfg, dry_mode)
        else:
            logging.info("tests.mcad.prepare_nodes=False, skipping.")

        if not dry_mode:
            if capture_prom:
                run.run("./run_toolbox.py cluster reset_prometheus_db > /dev/null")

            run.run(f"./run_toolbox.py from_config codeflare cleanup_appwrappers")

    next_count = env.next_artifact_index()
    with env.TempArtifactDir(env.ARTIFACT_DIR / f"{next_count:03d}__mcad_load_test"):

        test_artifact_dir_p[0] = env.ARTIFACT_DIR
        save_matbench_files(name, cfg)

        extra = {}
        failed = False
        try:
            configs = [
                ("states", "target"),
                ("states", "unexpected"),
                ("job", "template_name"),
                ("pod", "count"),
                ("pod", "runtime"),
                ("pod", "requests"),
            ]

            for (group, key) in configs:
                if not key in cfg["aw"].get(group, {}): continue
                extra[f"{group}_{key}"] = cfg["aw"][group][key]

            extra["aw_base_name"] = name
            extra["timespan"] = cfg["timespan"]
            extra["aw_count"] = cfg["aw"]["count"]
            extra["timespan"] = cfg["timespan"]

            job_mode = cfg["aw"]["job"].get("job_mode")

            extra["job_mode"] = bool(job_mode)

            if dry_mode:
                logging.info(f"Running the load test '{name}' with {extra} {'in Job mode' if job_mode else ''} ...")
                return

            try:
                run.run(f"./run_toolbox.py from_config codeflare generate_mcad_load --extra \"{extra}\"")
            except Exception as e:
                failed = True
                logging.error(f"*** Caught an exception during generate_mcad_load({name}): {e.__class__.__name__}: {e}")

        finally:
            with open(env.ARTIFACT_DIR / "exit_code", "w") as f:
                print("1" if failed else "0", file=f)

            if not dry_mode:
                try:
                    run.run(f"./run_toolbox.py from_config codeflare cleanup_appwrappers")
                except Exception as e:
                    logging.error(f"*** Caught an exception during cleanup_appwrappers({name}): {e.__class__.__name__}: {e}")
                    failed = True

                if capture_prom:
                    try:
                        run.run("./run_toolbox.py cluster dump_prometheus_db >/dev/null")
                    except Exception as e:
                        logging.error(f"*** Caught an exception during dump_prometheus_db({name}): {e.__class__.__name__}: {e}")
                        failed = True

                # must be part of the test directory
                run.run("./run_toolbox.py cluster capture_environment >/dev/null")

    logging.info(f"_run_test: Test '{name}' {'failed' if failed else 'passed'}.")

    return failed


def _run_test_and_visualize(name, test_override_values=None):
    failed = True
    do_test_multiple_values = test_override_values is None and config.ci_artifacts.get_config("tests.mcad.test_multiple_values.enabled")
    try:
        test_artifact_dir_p = [None]
        if do_test_multiple_values:
            failed = _run_test_multiple_values(name, test_artifact_dir_p)
        else:
            failed = _run_test(name, test_artifact_dir_p, test_override_values)
    finally:
        dry_mode = config.ci_artifacts.get_config("tests.mcad.dry_mode")
        if not config.ci_artifacts.get_config("tests.mcad.visualize"):
            logging.info(f"Visualization disabled.")

        elif dry_mode:
            logging.info(f"Running in dry mode, skipping the visualization.")

        elif test_artifact_dir_p[0] is not None:
            next_count = env.next_artifact_index()
            with env.TempArtifactDir(env.ARTIFACT_DIR / f"{next_count:03d}__plots"):
                visualize.prepare_matbench()

                if do_test_multiple_values:
                    matbench_config_file = config.ci_artifacts.get_config("tests.mcad.test_multiple_values.matbench_config_file")
                    with config.TempValue(config.ci_artifacts, "matbench.config_file", matbench_config_file):
                        generate_plots(test_artifact_dir_p[0])
                else:
                    generate_plots(test_artifact_dir_p[0])
        else:
            logging.warning("Not generating the visualization as the test artifact directory hasn't been created.")

    logging.info(f"_run_test_and_visualize: Test '{name}' {'failed' if failed else 'passed'}.")
    return failed

@entrypoint()
def test_ci(name=None, dry_mode=None, visualize=None, capture_prom=None, prepare_nodes=None):
    """
    Runs the test from the CI

    Args:
      name: name of the test to run. If empty, run all the tests of the configuration file
      dry_mode: if True, do not execute the tests, only list what would be executed
      visualize: if False, do not generate the visualization reports
      capture_prom: if False, do not capture Prometheus database
      prepare_nodes: if False, do not scale up the cluster nodes
    """


    if dry_mode is not None:
        config.ci_artifacts.set_config("tests.mcad.dry_mode", dry_mode)
    if visualize is not None:
        config.ci_artifacts.set_config("tests.mcad.visualize", visualize)
    if capture_prom is not None:
        config.ci_artifacts.set_config("tests.mcad.capture_prom", capture_prom)
    if prepare_nodes is not None:
        config.ci_artifacts.set_config("tests.mcad.prepare_nodes", prepare_nodes)

    try:
        failed_tests = []
        tests_to_run = config.ci_artifacts.get_config("tests.mcad.tests_to_run") \
            if not name else [name]

        for name in tests_to_run:
            next_count = env.next_artifact_index()
            with env.TempArtifactDir(env.ARTIFACT_DIR / f"{next_count:03d}__test-case_{name}"):
                try:
                    failed = _run_test_and_visualize(name)
                    if failed:
                        failed_tests.append(name)
                except Exception as e:
                    failed_tests.append(name)
                    logging.error(f"*** Caught an exception during _run_test_and_visualize({name}): {e.__class__.__name__}: {e}")
                    traceback.print_exc()

                    with open(env.ARTIFACT_DIR / "FAILURE", "w") as f:
                        print(traceback.format_exc(), file=f)

                    import bdb
                    if isinstance(e, bdb.BdbQuit):
                        raise

                if failed_tests and config.ci_artifacts.get_config("tests.mcad.stop_on_error"):
                    logging.info("Error detected, and tests.mcad.stop_on_error is set. Aborting.")
                    break

        if failed_tests:
            with open(env.ARTIFACT_DIR / "FAILED_TESTS", "w") as f:
                print("\n".join(failed_tests), file=f)

            msg = f"Caught exception(s) in [{', '.join(failed_tests)}], aborting."
            logging.error(msg)

            raise RuntimeError(msg)
    finally:
        run.run(f"testing/utils/generate_plot_index.py > {env.ARTIFACT_DIR}/report_index.html", check=False)

        if config.ci_artifacts.get_config("clusters.cleanup_on_exit"):
            cleanup_cluster()

# ---

@entrypoint(ignore_secret_path=True, apply_preset_from_pr_args=False)
def generate_plots_from_pr_args():
    """
    Generates the visualization reports from the PR arguments
    """

    visualize.download_and_generate_visualizations()


@entrypoint(ignore_secret_path=True)
def generate_plots(results_dirname):
    visualize.generate_from_dir(str(results_dirname))


# ---

@entrypoint()
def prepare_ci():
    """
    Prepares the cluster and the namespace for running the tests
    """

    return prepare.prepare_ci()


@entrypoint()
def cleanup_cluster():
    """
    Restores the cluster to its original state
    """

    return prepare.cleanup_cluster()

# ---

class Entrypoint:
    """
    Commands for launching the CI tests
    """

    def __init__(self):
        self.cleanup_cluster_ci = cleanup_cluster

        self.prepare_ci = prepare_ci
        self.test_ci = test_ci

        self.generate_plots_from_pr_args = generate_plots_from_pr_args
        self.generate_plots = generate_plots

def main():
    # Print help rather than opening a pager
    fire.core.Display = lambda lines, out: print(*lines, file=out)

    fire.Fire(Entrypoint())


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        logging.error(f"Command '{e.cmd}' failed --> {e.returncode}")
        sys.exit(1)
    except KeyboardInterrupt:
        print() # empty line after ^C
        logging.error(f"Interrupted.")
        sys.exit(1)
