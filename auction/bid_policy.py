import math
import time

class AgentBidPolicy:
    def __init__(self, agent, intersection_center=(-188.9, -89.7, 0.0)):
        self.agent = agent
        self.intersection_center = intersection_center
        
    def compute_bid(self):
        """
        路口竞价策略：针对路口通行优化
        返回出价值（越高优先级越高）
        """
        # 基础因子
        urgency = self._estimate_urgency()
        position_advantage = self._calculate_position_advantage()
        speed_factor = self._calculate_speed_factor()
        safety_factor = self._calculate_safety_factor()
        conflict_penalty = self._calculate_conflict_penalty()
        
        # 车队优势因子
        platoon_bonus = self._get_platoon_bonus()
        
        # 路口状态奖励/惩罚
        junction_factor = self._get_junction_factor()
        
        # 加权计算最终出价
        base_bid = (urgency * 20 +               # 方向紧急性权重
                   position_advantage * 15 +     # 位置优势权重  
                   speed_factor * 10 +           # 速度因子权重
                   safety_factor * 12 +          # 安全因子权重
                   platoon_bonus +               # 车队奖励
                   junction_factor * 8)          # 路口状态因子
        
        # 冲突惩罚
        final_bid = base_bid - conflict_penalty
        
        return max(0.0, final_bid)

    def _calculate_position_advantage(self):
        """计算位置优势：已在路口 > 即将进入路口 > 距离较远"""
        if self._is_platoon():
            leader = self.agent['vehicles'][0]
            at_junction = self.agent.get('at_junction', False)
            distance = self._distance_to_intersection(leader)
        else:
            at_junction = self.agent.get('at_junction', False)
            distance = self._distance_to_intersection(self.agent['data'])
        
        if at_junction:
            return 20.0  # 已在路口内，最高优势
        elif distance <= 15.0:
            return 15.0 - distance  # 越近优势越大
        elif distance <= 25.0:
            return 10.0 - (distance - 15.0) * 0.5
        else:
            return 0.0

    def _get_junction_factor(self):
        """路口状态因子：在路口内的车辆有完成通行的紧迫性"""
        if self._is_platoon():
            at_junction = self.agent.get('at_junction', False)
        else:
            at_junction = self.agent.get('at_junction', False)
        
        if at_junction:
            return 15.0  # 在路口内需要尽快完成通行
        else:
            return 0.0

    def _calculate_conflict_penalty(self):
        """计算冲突惩罚：左转与直行/右转的冲突"""
        direction = self._get_goal_direction()
        
        # 左转与其他方向冲突更多
        if direction == 'left':
            return 5.0  # 左转惩罚
        elif direction == 'right':
            return 0.0  # 右转最少冲突
        else:  # straight
            return 2.0  # 直行中等冲突

    def _estimate_urgency(self):
        """估计方向紧急性：右转 > 直行 > 左转"""
        direction = self._get_goal_direction()
        
        urgency_map = {
            'right': 5.0,    # 右转最容易，优先级高
            'straight': 3.0, # 直行中等
            'left': 1.5      # 左转最复杂，优先级低
        }
        
        return urgency_map.get(direction, 2.0)

    def _calculate_speed_factor(self):
        """计算速度因子：合理速度有优势"""
        if self._is_platoon():
            leader = self.agent['vehicles'][0]
            speed = self._get_current_speed(leader)
        else:
            speed = self._get_current_speed(self.agent['data'])
        
        # 路口适宜的速度范围
        if 2.0 <= speed <= 8.0:  # 合理通行速度
            return 8.0
        elif speed < 2.0:  # 速度过慢
            return 3.0
        else:  # 速度过快
            return 5.0

    def _calculate_safety_factor(self):
        """计算安全因子：确保安全通行的车辆有更高优先级"""
        if self._is_platoon():
            platoon_size = len(self.agent['vehicles'])
            if platoon_size <= 3:  # 小车队更安全
                return 10.0
            else:  # 大车队需要更多时间
                return 6.0
        else:
            # 单车安全因子
            speed = self._get_current_speed(self.agent['data'])
            if 2.0 <= speed <= 10.0:  # 合理速度范围
                return 8.0
            else:
                return 4.0

    def _get_platoon_bonus(self):
        """车队奖励：鼓励车队协调通行"""
        if self._is_platoon():
            platoon_size = len(self.agent['vehicles'])
            
            # 车队协调通行效益
            if platoon_size == 2:
                return 10.0
            elif platoon_size == 3:
                return 15.0
            elif platoon_size >= 4:
                return 18.0
            else:
                return 0.0
        return 0.0

    def _get_goal_direction(self):
        """获取目标方向"""
        if self._is_platoon():
            return self.agent.get('goal_direction', 'straight')
        else:
            # 单车需要从状态推断方向
            return self._infer_direction_from_state()

    def _infer_direction_from_state(self):
        """从车辆状态推断行驶方向"""
        # 简化版本：随机分配（实际应用中应该基于路径规划）
        import random
        return random.choice(['left', 'straight', 'right'])

    def _is_platoon(self):
        """判断是否为车队"""
        return 'vehicles' in self.agent and len(self.agent['vehicles']) > 1

    def _get_current_speed(self, vehicle_state):
        """获取当前速度"""
        velocity = vehicle_state.get('velocity', (0, 0, 0))
        return math.sqrt(velocity[0]**2 + velocity[1]**2)

    def _distance_to_intersection(self, vehicle_state):
        """计算到交叉口的距离"""
        location = vehicle_state.get('location', (0, 0, 0))
        dx = location[0] - self.intersection_center[0]
        dy = location[1] - self.intersection_center[1]
        return math.sqrt(dx*dx + dy*dy)
