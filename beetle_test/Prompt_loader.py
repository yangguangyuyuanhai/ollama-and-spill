import yaml
import logging
import os

logger = logging.getLogger(__name__)

class PromptLoader:
    def __init__(self, file_path='./promot/spill_promot.yaml'):
        self.file_path = file_path
        self.config = {}
        self.load_config()

    #######加载提示词#########
    def load_config(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    self.config = yaml.safe_load(f)
                logger.info(f"load_config running")
            except Exception as e:
                logger.debug(f"maybe spill_promopt is error,this is {e}")
        else:
            logger.warning(f"spill_promot.yaml is not found{self.file_path}")
    #######加载完成##########

    def system_prompt_get(self, mission_type:str) -> str:
        prompt = self.config.get(mission_type)
        if prompt:
            return str(prompt)
        else:
            return str(self.config.get('is_spill'))

