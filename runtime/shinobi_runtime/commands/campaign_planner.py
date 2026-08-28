"""Production alias for the single Jianghu planner."""
from .planner import RepositoryCommandPlanner
class CampaignCommandPlanner(RepositoryCommandPlanner):
    pass
__all__=['CampaignCommandPlanner']
