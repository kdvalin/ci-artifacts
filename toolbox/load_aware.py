import sys

from toolbox._common import RunAnsibleRole, AnsibleRole, AnsibleMappedParams, AnsibleConstant, AnsibleSkipConfigGeneration


class LoadAware:
    """
    Commands relating to Trimaran and LoadAware testing
    """

    @AnsibleRole("load_aware_deploy_trimaran")
    @AnsibleMappedParams
    def deploy_trimaran(self):
        """
        Role to deploy the Trimaran load aware scheduler

        Args:
            None
        """

        return RunAnsibleRole(locals())

    @AnsibleRole("load_aware_undeploy_trimaran")
    @AnsibleMappedParams
    def undeploy_trimaran(self):
        """
        Role to undeploy the Trimaran load aware scheduler
        
        Args:
            None
        """

        return RunAnsibleRole(locals())
