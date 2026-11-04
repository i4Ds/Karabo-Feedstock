
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "detect_changed_dependencies.py"
)

spec = importlib.util.spec_from_file_location(
    "detect_changed_dependencies", SCRIPT
)
detector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = detector
spec.loader.exec_module(detector)


class DependencyDetectionTests(unittest.TestCase):

    def check_version_change(self, variable, dependency):
        build_base = ".github/workflows/build_base.yml"

        before = f"env:\n  {variable}: 1.0.0\n"
        after = f"env:\n  {variable}: 2.0.0\n"

        def fake_file(revision, path):
            return before if revision == "base" else after

        with patch.object(detector, "changed_files", return_value={build_base}):
            with patch.object(detector, "file_at_revision", side_effect=fake_file):
                result = detector.detect("base", "head")

        self.assertTrue(result[dependency])
        for other in ("idg", "aotools", "tools21cm", "seqfile", "eidos"):
            if other != dependency:
                self.assertFalse(result[other])

    def test_idg_version_change(self):
        self.check_version_change("IDG_VERSION", "idg")

    def test_aotools_version_change(self):
        self.check_version_change("AOTOOLS_VERSION", "aotools")

    def test_tools21cm_version_change(self):
        self.check_version_change("TOOLS_21CM_VERSION", "tools21cm")

    def test_idg_recipe_change(self):
        with patch.object(
            detector, "changed_files", return_value={"idg/meta.yaml"}
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["idg"])
        self.assertFalse(result["aotools"])
        self.assertFalse(result["tools21cm"])
        self.assertFalse(result["seqfile"])
        self.assertFalse(result["eidos"])

    def test_shared_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/workflows/build_base.yml"},
        ):
            with patch.object(
                detector,
                "file_at_revision",
                side_effect=[
                    "env:\n  IDG_VERSION: 1.2.0\njobs:\n  old: true\n",
                    "env:\n  IDG_VERSION: 1.2.0\njobs:\n  new: true\n",
                ],
            ):
                result = detector.detect("base", "head")

        self.assertTrue(result["idg"])
        self.assertTrue(result["aotools"])
        self.assertTrue(result["tools21cm"])
        self.assertTrue(result["seqfile"])
        self.assertTrue(result["eidos"])
        self.assertTrue(result["ska-telmodel"])
        self.assertTrue(result["pybdsf"])
        self.assertTrue(result["everybeam"])
        self.assertTrue(result["wsclean"])
        self.assertTrue(result["ska-sdp-datamodels"])
        self.assertTrue(result["ska-sdp-func"])
        self.assertTrue(result["ska-sdp-func-python"])
        self.assertTrue(result["aratmospy"])

    def test_idg_build_script_change(self):
        with patch.object(
            detector, "changed_files", return_value={"idg/build.sh"}
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["idg"])
        self.assertFalse(result["aotools"])
        self.assertFalse(result["tools21cm"])
        self.assertFalse(result["seqfile"])
        self.assertFalse(result["eidos"])
        self.assertFalse(result["ska-telmodel"])

    def test_idg_recipe_patch_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"idg/patches/fix.patch"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["idg"])
        self.assertFalse(result["aotools"])
        self.assertFalse(result["tools21cm"])

    def test_unknown_version_variable_fails(self):
        build_base = ".github/workflows/build_base.yml"

        before = "env:\n  UNKNOWN_VERSION: 1.0.0\n"
        after = "env:\n  UNKNOWN_VERSION: 2.0.0\n"

        with patch.object(
            detector, "changed_files", return_value={build_base}
        ):
            with patch.object(
                detector,
                "file_at_revision",
                side_effect=[before, after],
            ):
                with self.assertRaisesRegex(
                    ValueError, "Unregistered version variables"
                ):
                    detector.detect("base", "head")

    def test_dependency_registry_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/dependency-scan.toml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["idg"])
        self.assertTrue(result["aotools"])
        self.assertTrue(result["tools21cm"])
        self.assertTrue(result["seqfile"])
        self.assertTrue(result["eidos"])

    def test_idg_build_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/workflows/build_idg.yml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["idg"])
        self.assertFalse(result["aotools"])
        self.assertFalse(result["tools21cm"])
        self.assertFalse(result["seqfile"])
        self.assertFalse(result["eidos"])
    def test_detector_change_triggers_all_dependencies(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={
                ".github/scripts/detect_changed_dependencies.py"
            },
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["idg"])
        self.assertTrue(result["aotools"])
        self.assertTrue(result["tools21cm"])
        self.assertTrue(result["seqfile"])
        self.assertTrue(result["eidos"])

    def test_unused_dependencies_are_excluded(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/dependency-scan.toml"},
        ):
            result = detector.detect("base", "head")

        for dependency_id in detector.EXCLUDED_DEPENDENCIES:
            with self.subTest(dependency=dependency_id):
                self.assertIn(dependency_id, result)
                self.assertFalse(result[dependency_id])

        self.assertTrue(result["idg"])
        self.assertTrue(result["aotools"])
        self.assertTrue(result["tools21cm"])
        self.assertTrue(result["seqfile"])
        self.assertTrue(result["eidos"])
        self.assertTrue(result["wsclean"])

    def test_aotools_release_test_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/scripts/tests/test_aotools_release.py"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["aotools"])
        self.assertFalse(result["idg"])
        self.assertFalse(result["tools21cm"])

    def test_tools21cm_release_test_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/scripts/tests/test_tools21cm_release.py"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["tools21cm"])
        self.assertFalse(result["idg"])
        self.assertFalse(result["aotools"])

    def test_seqfile_version_change(self):
        self.check_version_change("SEQFILE_VERSION", "seqfile")

    def test_seqfile_recipe_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"seqfile/meta.yaml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["seqfile"])
        for other in ("idg", "aotools", "tools21cm", "eidos"):
            self.assertFalse(result[other])

    def test_seqfile_build_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/workflows/build_seqfile.yml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["seqfile"])
        for other in ("idg", "aotools", "tools21cm", "eidos"):
            self.assertFalse(result[other])

    def test_detector_test_change_triggers_all_dependencies(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={
                ".github/scripts/tests/test_detect_changed_dependencies.py"
            },
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["idg"])
        self.assertTrue(result["aotools"])
        self.assertTrue(result["tools21cm"])
        self.assertTrue(result["seqfile"])
        self.assertTrue(result["eidos"])

    def test_eidos_version_change(self):
        self.check_version_change("EIDOS_VERSION", "eidos")

    def test_eidos_recipe_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"eidos/meta.yaml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["eidos"])
        for other in ("idg", "aotools", "tools21cm", "seqfile"):
            self.assertFalse(result[other])

    def test_eidos_build_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/workflows/build_eidos.yml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["eidos"])
        for other in ("idg", "aotools", "tools21cm", "seqfile"):
            self.assertFalse(result[other])

    def test_eidos_build_script_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"eidos/build.sh"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["eidos"])
        for other in ("idg", "aotools", "tools21cm", "seqfile"):
            self.assertFalse(result[other])

    def test_ska_telmodel_version_change(self):
        self.check_version_change("SKA_TELMODEL_VERSION", "ska-telmodel")

    def test_ska_telmodel_version_change(self):
        self.check_version_change("SKA_TELMODEL_VERSION", "ska-telmodel")

    def test_ska_telmodel_version_change(self):
        self.check_version_change("SKA_TELMODEL_VERSION", "ska-telmodel")

    def test_ska_telmodel_recipe_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"ska-telmodel/meta.yaml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["ska-telmodel"])
        for other in ("idg", "aotools", "tools21cm", "seqfile", "eidos"):
            self.assertFalse(result[other])

    def test_ska_telmodel_build_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/workflows/build_SKA_Telmodel.yml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["ska-telmodel"])
        for other in ("idg", "aotools", "tools21cm", "seqfile", "eidos"):
            self.assertFalse(result[other])

    def test_ska_telmodel_build_script_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"ska-telmodel/build.sh"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["ska-telmodel"])
        for other in ("idg", "aotools", "tools21cm", "seqfile", "eidos"):
            self.assertFalse(result[other])
    def test_pybdsf_version_change(self):
        self.check_version_change("PYBDSF_VERSION", "pybdsf")

    def test_pybdsf_recipe_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"pybdsf/meta.yaml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["pybdsf"])
        for other in (
            "idg", "aotools", "tools21cm",
            "seqfile", "eidos", "ska-telmodel",
        ):
            self.assertFalse(result[other])

    def test_pybdsf_build_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/workflows/build_PyBDSF.yml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["pybdsf"])
        for other in (
            "idg", "aotools", "tools21cm",
            "seqfile", "eidos", "ska-telmodel",
        ):
            self.assertFalse(result[other])

    def test_pybdsf_build_script_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"pybdsf/build.sh"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["pybdsf"])
        for other in (
            "idg", "aotools", "tools21cm",
            "seqfile", "eidos", "ska-telmodel",
        ):
            self.assertFalse(result[other])

    def test_everybeam_version_change(self):
        self.check_version_change("EVERYBEAM_VERSION", "everybeam")

    def test_everybeam_recipe_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"everybeam/meta.yaml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["everybeam"])
        for other in (
            "idg", "aotools", "tools21cm", "seqfile",
            "eidos", "ska-telmodel", "pybdsf",
        ):
            self.assertFalse(result[other])

    def test_everybeam_build_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/workflows/build_everybeam.yml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["everybeam"])
        for other in (
            "idg", "aotools", "tools21cm", "seqfile",
            "eidos", "ska-telmodel", "pybdsf",
        ):
            self.assertFalse(result[other])

    def test_everybeam_build_script_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"everybeam/build.sh"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["everybeam"])
        for other in (
            "idg", "aotools", "tools21cm", "seqfile",
            "eidos", "ska-telmodel", "pybdsf",
        ):
            self.assertFalse(result[other])

    def test_wsclean_version_change(self):
        self.check_version_change("WSCLEAN_VERSION", "wsclean")

    def test_wsclean_recipe_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"wsclean/meta.yaml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["wsclean"])

    def test_wsclean_build_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/workflows/build_wsclean.yml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["wsclean"])

    def test_wsclean_build_script_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"wsclean/build.sh"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["wsclean"])
    def test_ska_sdp_datamodels_version_change(self):
        self.check_version_change(
            "SKA_SDP_DTMDL_VERSION",
            "ska-sdp-datamodels",
        )

    def test_ska_sdp_datamodels_recipe_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"ska-sdp-datamodels/meta.yaml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["ska-sdp-datamodels"])

    def test_ska_sdp_datamodels_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={
                ".github/workflows/build_SKA_SDP_Datamodel.yml"
            },
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["ska-sdp-datamodels"])

    def test_ska_sdp_datamodels_build_script_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"ska-sdp-datamodels/build.sh"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["ska-sdp-datamodels"])
    def test_ska_sdp_func_version_change(self):
        self.check_version_change(
            "SKA_SDP_FUNC_VERSION", "ska-sdp-func"
        )

    def test_ska_sdp_func_python_version_change(self):
        self.check_version_change(
            "SKA_SDP_FUNC_PY_VERSION", "ska-sdp-func-python"
        )

    def test_ska_sdp_func_recipe_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"ska-sdp-func/meta.yaml"},
        ):
            result = detector.detect("base", "head")
        self.assertTrue(result["ska-sdp-func"])

    def test_ska_sdp_func_python_recipe_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"ska-sdp-func-python/meta.yaml"},
        ):
            result = detector.detect("base", "head")
        self.assertTrue(result["ska-sdp-func-python"])

    def test_ska_sdp_func_native_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={
                ".github/workflows/build_SKA_SDP_Func_Native.yml"
            },
        ):
            result = detector.detect("base", "head")
        self.assertTrue(result["ska-sdp-func"])

    def test_ska_sdp_func_python_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={
                ".github/workflows/build_SKA_SDP_Func_Python.yml"
            },
        ):
            result = detector.detect("base", "head")
        self.assertTrue(result["ska-sdp-func-python"])

    def test_ska_sdp_func_native_build_script_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"ska-sdp-func/build.sh"},
        ):
            result = detector.detect("base", "head")
        self.assertTrue(result["ska-sdp-func"])

    def test_ska_sdp_func_python_build_script_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"ska-sdp-func-python/build.sh"},
        ):
            result = detector.detect("base", "head")
        self.assertTrue(result["ska-sdp-func-python"])

    def test_ska_sdp_func_native_change_triggers_python_wrapper(self):
        self.check_version_change(
            "SKA_SDP_FUNC_VERSION",
            "ska-sdp-func-python",
        )
    def test_aratmospy_version_change(self):
        self.check_version_change("ARATMOSPY_VERSION", "aratmospy")

    def test_aratmospy_recipe_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"aratmospy/meta.yaml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["aratmospy"])

    def test_aratmospy_build_workflow_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={".github/workflows/build_ARatmospy.yml"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["aratmospy"])

    def test_aratmospy_build_script_change(self):
        with patch.object(
            detector,
            "changed_files",
            return_value={"aratmospy/build.sh"},
        ):
            result = detector.detect("base", "head")

        self.assertTrue(result["aratmospy"])

if __name__ == "__main__":
    unittest.main()
