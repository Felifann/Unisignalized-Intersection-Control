import time
import math
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum

from env.simulation_config import SimulationConfig
from .bid_policy import AgentBidPolicy

class AuctionStatus(Enum):
    WAITING = "waiting"
    BIDDING = "bidding" 
    EVALUATING = "evaluating"
    COMPLETED = "completed"

@dataclass
class AuctionAgent:
    """Represents an agent (vehicle or platoon) participating in the auction"""
    id: str
    type: str  # 'vehicle' or 'platoon'
    location: Tuple[float, float, float]
    data: Dict[str, Any]
    at_junction: bool = False
    
    @property
    def vehicles(self) -> List[Dict]:
        """Get vehicles associated with this agent"""
        if self.type == 'platoon':
            return self.data.get('vehicles', [])
        return [self.data] if self.type == 'vehicle' else []

@dataclass
class Bid:
    """Represents a bid in the auction"""
    participant_id: str
    value: float
    timestamp: float
    participant: AuctionAgent

@dataclass
class AuctionWinner:
    """Represents an auction winner with ranking"""
    participant: AuctionAgent
    bid: Bid
    rank: int
    # protected: bool = False
    conflict_action: str = "go"

class Auction:
    """Manages a single auction round"""
    
    def __init__(self, auction_id: str, agents: List[AuctionAgent], 
                 bidding_duration: float = 1.0):
        self.id = auction_id
        self.agents = agents
        self.start_time = time.time()
        self.deadline = self.start_time + bidding_duration
        self.status = AuctionStatus.BIDDING
        self.bids: Dict[str, Bid] = {}
        self.winners: List[AuctionWinner] = []
    
    def add_bid(self, bid: Bid) -> bool:
        """Add a bid to the auction"""
        if self.status != AuctionStatus.BIDDING:
            return False
        
        self.bids[bid.participant_id] = bid
        return True
    
    def is_expired(self) -> bool:
        """Check if auction has expired"""
        return time.time() >= self.deadline
    
    def get_participation_rate(self) -> float:
        """Get percentage of agents who have bid"""
        if not self.agents:
            return 0.0
        return len(self.bids) / len(self.agents)

class LaneGrouper:
    """Handles lane-based vehicle grouping logic"""
    
    def __init__(self, state_extractor=None):
        self.state_extractor = state_extractor
    
    def get_lane_leaders(self, vehicle_states: List[Dict]) -> List[Dict]:
        """Get the first vehicle in each lane approaching the intersection"""
        lanes = self._group_vehicles_by_lane(vehicle_states)
        lane_leaders = []
        
        for lane_id, vehicles in lanes.items():
            if vehicles:
                # Find closest vehicle to intersection in this lane
                closest_vehicle = min(
                    vehicles,
                    key=lambda v: SimulationConfig.distance_to_intersection_center(v['location'])
                )
                lane_leaders.append(closest_vehicle)
        
        return lane_leaders
    
    def _group_vehicles_by_lane(self, vehicle_states: List[Dict]) -> Dict[str, List[Dict]]:
        """Group vehicles by lane using CARLA waypoint system"""
        lanes = {}
        
        for vehicle in vehicle_states:
            try:
                if self.state_extractor:
                    import carla
                    location = carla.Location(
                        x=vehicle['location'][0],
                        y=vehicle['location'][1], 
                        z=vehicle['location'][2]
                    )
                    waypoint = self.state_extractor.carla.world.get_map().get_waypoint(location)
                    lane_key = f"road_{waypoint.road_id}_lane_{waypoint.lane_id}"
                    
                    if lane_key not in lanes:
                        lanes[lane_key] = []
                    lanes[lane_key].append(vehicle)
                    
            except Exception as e:
                print(f"[LaneGrouper] Error getting lane info for vehicle {vehicle['id']}: {e}")
        
        return lanes

class ParticipantIdentifier:
    """Identifies auction agents from vehicle states and platoons"""
    
    def __init__(self, lane_grouper: LaneGrouper):
        self.lane_grouper = lane_grouper
    
    def identify_agents(self, vehicle_states: List[Dict], 
                            platoon_manager=None) -> List[AuctionAgent]:
        """识别拍卖参与者 - 支持车队和单车混合模式"""
        agents = []
        platoon_vehicle_ids = set()
        
        # 1. 首先添加车队agent（如果有车队管理器）
        if platoon_manager:
            platoons = platoon_manager.get_all_platoons()
            for platoon in platoons:
                if platoon.is_valid() and len(platoon.vehicles) >= 2:
                    # 检查车队是否应该参与拍卖
                    if self._should_platoon_participate(platoon):
                        agent = AuctionAgent(
                            id=platoon.platoon_id,
                            type='platoon',
                            location=tuple(platoon.get_leader_position() or (0, 0, 0)),
                            data={'vehicles': platoon.vehicles, 'platoon': platoon},
                            at_junction=platoon.has_vehicle_in_intersection()
                        )
                        agents.append(agent)
                        platoon_vehicle_ids.update(platoon.get_vehicle_ids())
        
        # 2. 添加独立车辆agent（排除已在车队中的车辆）
        lane_leaders = self.lane_grouper.get_lane_leaders(vehicle_states)
        
        for vehicle in lane_leaders:
            vehicle_id = str(vehicle['id'])
            if vehicle_id in platoon_vehicle_ids:
                continue
            if self._is_vehicle_actively_passing(vehicle):
                continue
            if self._vehicle_has_destination(vehicle):
                agent = AuctionAgent(
                    id=vehicle['id'],
                    type='vehicle',
                    location=vehicle['location'],
                    data=vehicle,
                    at_junction=vehicle.get('is_junction', False)
                )
                agents.append(agent)
        
        return agents
    
    def _should_platoon_participate(self, platoon) -> bool:
        """检查车队是否应该参与拍卖 - 修复逻辑错误"""
        # 检查车队是否有效且有足够车辆
        if not platoon.is_valid() or platoon.get_size() < 2:
            return False
        
        # 车队领头车辆应该接近路口
        leader_location = platoon.get_leader_position()
        if not leader_location:
            return False
        
        # 检查距离路口的距离
        distance_to_intersection = math.sqrt(
            (leader_location[0] - (-188.9))**2 + 
            (leader_location[1] - (-89.7))**2
        )
        
        # 更宽松的距离要求和准备状态检查
        distance_ok = distance_to_intersection < 100.0  # 增加距离阈值
        
        # SIMPLIFIED: 不要求过于严格的准备状态
        ready_for_intersection = True  # 简化准备检查，便于调试
        
        should_participate = distance_ok and ready_for_intersection
        
        if should_participate:
            print(f"🚛 Platoon {platoon.platoon_id} eligible for auction: "
                  f"distance={distance_to_intersection:.1f}m, size={platoon.get_size()}")
        
        return should_participate
    
    def _is_vehicle_actively_passing(self, vehicle: Dict) -> bool:
        """检查车辆是否正在积极通过路口（而非仅仅在路口边界等待）"""
        # 如果车辆不在路口区域，肯定不是在通过
        if not vehicle.get('is_junction', False):
            return False
        
        # 检查车辆是否有显著的速度（正在移动通过路口）
        velocity = vehicle.get('velocity', [0, 0, 0])
        if isinstance(velocity, (list, tuple)) and len(velocity) >= 2:
            speed = math.sqrt(velocity[0]**2 + velocity[1]**2)
            # 如果车辆在路口内且速度大于阈值，认为正在通过
            if speed > 1.0:  # 2 m/s threshold for "actively passing"
                return True
        
        # 否则，即使在路口区域，也可能只是在边界等待
        return False
    
        # DISABLED: Platoon-related methods temporarily removed
        # def _analyze_platoon_transit_status(self, platoon_vehicles: List[Dict]) -> Dict:
        # def _get_vehicle_lane(self, vehicle: Dict) -> str:
    
    def _vehicle_has_destination(self, vehicle: Dict) -> bool:
        """Check if vehicle has a valid destination set"""
        try:
            # Check if vehicle has destination in its data
            if 'destination' in vehicle and vehicle['destination'] is not None:
                return True
            
            # Check if vehicle is moving (has non-zero velocity)
            velocity = vehicle.get('velocity', [0, 0, 0])
            if isinstance(velocity, (list, tuple)) and len(velocity) >= 2:
                speed = math.sqrt(velocity[0]**2 + velocity[1]**2)
                return speed > 0.1  # Moving vehicles likely have destinations
            
            # Default: assume vehicle has destination if it's in the simulation
            return True
            
        except Exception as e:
            print(f"[Warning] 检查车辆目的地失败 {vehicle.get('id', 'unknown')}: {e}")
            return True  # Default to True to include vehicle in auction

class AuctionEvaluator:
    """Handles auction evaluation and winner determination"""
    
    def __init__(self, intersection_center: Tuple[float, float, float]):
        self.intersection_center = intersection_center
        self.protected_agents: set = set()
        self.agents_in_transit: Dict[str, Dict] = {}
    
    def evaluate_auction(self, auction: Auction) -> List[AuctionWinner]:
        """Evaluate auction and determine winners with priority ranking"""
        if not auction.bids:
            return []
        
        # 1. Identify protected agents (already in transit)
        # protected_winners = self._get_protected_winners(auction.bids)
        
        # 2. Sort remaining bidders by bid value
        remaining_bids = {k: v for k, v in auction.bids.items() }
                         # if k not in [w.participant.id for w in protected_winners]}
        
        regular_winners = self._evaluate_regular_bids(remaining_bids)
        
        # 3. Combine and assign final rankings
        all_winners = regular_winners # + protected_winners
        for i, winner in enumerate(all_winners):
            winner.rank = i + 1
        
        auction.winners = all_winners
        return all_winners

    def _get_protected_winners(self, bids: Dict[str, Bid]) -> List[AuctionWinner]:
        """Get winners that are protected (in transit through intersection)"""
        protected_winners = []
        
        for bid in bids.values():
            if self._is_participant_in_transit(bid.participant):
                # Mark as protected
                self.protected_agents.add(bid.participant_id)
                self.agents_in_transit[bid.participant_id] = {
                    'start_time': time.time(),
                    'original_bid': bid.value
                }
                
                winner = AuctionWinner(
                    participant=bid.participant,
                    bid=bid,
                    rank=0,
                    protected=True
                )
                protected_winners.append(winner)
        
        return protected_winners
    
    def _evaluate_regular_bids(self, bids: Dict[str, Bid]) -> List[AuctionWinner]:
        """Evaluate regular (non-protected) bids"""
        if not bids:
            return []
        
        # Sort by bid value (descending), with tie-breaker by timestamp
        sorted_bids = sorted(
            bids.values(),
            key=lambda b: (b.value, -b.timestamp),  # Higher bid wins, earlier timestamp breaks ties
            reverse=True
        )
        
        winners = []
        for bid in sorted_bids:
            winner = AuctionWinner(
                participant=bid.participant,
                bid=bid,
                rank=0,
                #protected=False
            )
            winners.append(winner)
        
        return winners
    
    def _is_participant_in_transit(self, participant: AuctionAgent) -> bool:
        """Check if participant is currently in transit through intersection"""
        if participant.type == 'vehicle':
            return participant.data.get('is_junction', False)
        elif participant.type == 'platoon':
            # Platoon is in transit if any vehicle is in junction
            for vehicle in participant.vehicles:
                if vehicle.get('is_junction', False):
                    return True
        return False
    
    def cleanup_completed_agents(self, vehicle_states: List[Dict], platoon_manager=None):
        """Clean up agents that have completed transit - 支持车队和单车"""
        current_time = time.time()
        completed_agents = []
        
        for agent_id in list(self.protected_agents):
            # Check both single vehicles and platoons
            agent_still_in_transit = self._check_agent_still_in_transit(
                agent_id, vehicle_states, platoon_manager
            )
            
            # Remove protection if agent completed transit or timed out
            transit_time = current_time - self.agents_in_transit.get(agent_id, {}).get('start_time', current_time)
            
            if not agent_still_in_transit or transit_time > 30.0:
                completed_agents.append(agent_id)
        
        # Clean up completed agents
        for agent_id in completed_agents:
            self.protected_agents.discard(agent_id)
            self.agents_in_transit.pop(agent_id, None)
            agent_type = "车队" if agent_id.startswith("platoon_") else "车辆"
            print(f"✅ {agent_type} {agent_id} completed transit, protection removed")
    
    def _check_agent_still_in_transit(self, agent_id: str, vehicle_states: List[Dict], platoon_manager=None) -> bool:
        """检查agent是否仍在通过路口 - 支持车队和单车"""
        # Check if it's a platoon
        if agent_id.startswith("platoon_") and platoon_manager:
            return self._check_platoon_still_in_transit(agent_id, platoon_manager)
        else:
            return self._check_single_vehicle_in_transit(agent_id, vehicle_states)
    
    def _check_platoon_still_in_transit(self, platoon_id: str, platoon_manager) -> bool:
        """检查车队是否仍在通过路口"""
        # Find the platoon
        for platoon in platoon_manager.get_all_platoons():
            if platoon.platoon_id == platoon_id:
                return platoon.has_vehicle_in_intersection()
        
        # Platoon not found, consider it as completed
        return False
    
    def _check_single_vehicle_in_transit(self, agent_id: str, vehicle_states: List[Dict]) -> bool:
        """检查单车是否仍在通过路口 - 简化版本"""
        for vehicle_state in vehicle_states:
            vehicle_id = str(vehicle_state['id'])
            if vehicle_id == str(agent_id):
                return vehicle_state.get('is_junction', False)
        return False
    
    # DISABLED: Platoon-specific transit checking
    # def _check_agent_still_in_transit(self, agent_id: str, vehicle_states: List[Dict], platoon_manager) -> bool:

class DecentralizedAuctionEngine:
    """Main auction engine managing the complete auction process - 支持车队和单车"""
    
    def __init__(self, intersection_center=(-188.9, -89.7, 0.0), 
                 communication_range=50.0, state_extractor=None):
        self.intersection_center = intersection_center
        self.communication_range = communication_range
        self.state_extractor = state_extractor
        
        # Core components
        self.lane_grouper = LaneGrouper(state_extractor)
        self.participant_identifier = ParticipantIdentifier(self.lane_grouper)
        self.evaluator = AuctionEvaluator(intersection_center)
        
        # Auction management
        self.current_auction: Optional[Auction] = None
        self.auction_history: Dict[str, Auction] = {}
        self.auction_interval = 2.0  # seconds between auctions
        self.last_auction_time = 0
        
        # Communication simulation
        self.message_queue: List[Dict] = []
        
        # Integration points
        self.vehicle_enforcer = None
        
        print("🎯 增强拍卖引擎已初始化 - 支持车队和单车混合模式")
    
    def set_vehicle_enforcer(self, vehicle_enforcer):
        """Set vehicle control enforcer for integration"""
        self.vehicle_enforcer = vehicle_enforcer
    
    def update(self, vehicle_states: List[Dict], platoon_manager=None) -> List[AuctionWinner]:
        """Main update loop - 支持车队和单车混合模式"""
        current_time = time.time()
        
        # 1. Identify potential agents (vehicles and platoons)
        agents = self.participant_identifier.identify_agents(
            vehicle_states, platoon_manager
        )
        
        # 2. Start new auction if needed
        if agents and not self.current_auction:
            self._start_new_auction(agents, current_time)
        
        # 3. Process current auction
        winners = []
        if self.current_auction:
            winners = self._process_current_auction(current_time)
        
        # 4. Clean up completed protected agents (vehicles and platoons)
        self.evaluator.cleanup_completed_agents(vehicle_states, platoon_manager)
        
        # 5. Simulate communication
        self._simulate_v2v_communication()
        
        return winners
    
    def _start_new_auction(self, agents: List[AuctionAgent], start_time: float):
        """Start a new auction round"""
        auction_id = f"junction_auction_{int(start_time)}"
        self.current_auction = Auction(auction_id, agents)
        
        # Collect bids immediately
        self._collect_bids()
        
        # Broadcast auction start
        self._broadcast_message({
            'type': 'auction_start',
            'auction_id': auction_id,
            'agents': [a.id for a in agents],
            'timestamp': start_time
        })
        
        print(f"🎯 Started auction {auction_id} with {len(agents)} agents")
    
    def _process_current_auction(self, current_time: float) -> List[AuctionWinner]:
        """Process the current active auction"""
        if not self.current_auction:
            return []
        
        auction = self.current_auction
        
        if auction.status == AuctionStatus.BIDDING:
            if auction.is_expired():
                auction.status = AuctionStatus.EVALUATING
        
        elif auction.status == AuctionStatus.EVALUATING:
            winners = self.evaluator.evaluate_auction(auction)
            auction.status = AuctionStatus.COMPLETED
            
            # Broadcast results
            self._broadcast_auction_results(auction.id, winners)
            # self._print_auction_results(auction.id, winners)
            
            return winners
        
        elif auction.status == AuctionStatus.COMPLETED:
            # Archive and clean up
            self.auction_history[auction.id] = auction
            self.current_auction = None
            self.last_auction_time = current_time
        
        return auction.winners if auction.winners else []
    
    def _collect_bids(self):
        """Collect bids from all agents"""
        if not self.current_auction:
            return
        
        for agent in self.current_auction.agents:
            # Create bid policy and compute bid
            bid_policy = AgentBidPolicy(
                self._agent_to_dict(agent),
                self.intersection_center,
                self.state_extractor
            )
            bid_value = bid_policy.compute_bid()
            
            # Create and add bid
            bid = Bid(
                participant_id=agent.id,
                value=bid_value,
                timestamp=time.time(),
                participant=agent
            )
            
            self.current_auction.add_bid(bid)
    
    def _agent_to_dict(self, agent: AuctionAgent) -> Dict:
        """Convert AuctionAgent to dict format for BidPolicy"""
        agent_dict = {
            'id': agent.id,
            'type': agent.type,
            'location': agent.location,
            'at_junction': agent.at_junction
        }
        
        # Handle both vehicles and platoons
        if agent.type == 'vehicle':
            agent_dict['data'] = agent.data
        elif agent.type == 'platoon':
            agent_dict['data'] = agent.data
            vehicles = agent.data.get('vehicles', [])
            agent_dict['platoon_size'] = len(vehicles)
            agent_dict['vehicles'] = vehicles
            print(f"🎯 Platoon {agent.id} prepared for bidding: {len(vehicles)} vehicles")
        
        return agent_dict

    def _broadcast_auction_results(self, auction_id: str, winners: List[AuctionWinner]):
        """Broadcast auction results"""
        self._broadcast_message({
            'type': 'auction_results',
            'auction_id': auction_id,
            'winners': [(w.participant.id, w.bid.value, w.rank) for w in winners[:4]],
            'timestamp': time.time()
        })
    
    def _broadcast_message(self, message: Dict):
        """Add message to communication queue"""
        self.message_queue.append(message)
    
    def _simulate_v2v_communication(self):
        """Simulate V2V communication with delays and packet loss"""
        current_time = time.time()
        valid_messages = []
        
        for message in self.message_queue:
            # Keep messages valid for 0.5 seconds
            if current_time - message['timestamp'] < 0.5:
                valid_messages.append(message)
        
        self.message_queue = valid_messages
    
    def get_current_priority_order(self) -> List[AuctionWinner]:
        """Get current priority order from active or most recent auction"""
        if self.current_auction and self.current_auction.winners:
            return self.current_auction.winners
        
        # Return most recent completed auction results
        if self.auction_history:
            latest_auction = max(self.auction_history.values(), 
                               key=lambda a: a.start_time)
            return latest_auction.winners
        
        return []
    
    def get_auction_stats(self) -> Dict[str, Any]:
        """Get comprehensive auction statistics - 支持车队统计"""
        current_agents = 0
        vehicle_count = 0
        platoon_count = 0
        
        if self.current_auction:
            current_agents = len(self.current_auction.agents)
            for agent in self.current_auction.agents:
                if agent.type == 'vehicle':
                    vehicle_count += 1
                elif agent.type == 'platoon':
                    platoon_count += 1
        
        return {
            'active_auction': self.current_auction is not None,
            'current_agents': current_agents,
            'platoon_agents': platoon_count,
            'vehicle_agents': vehicle_count,
            'completed_auctions': len(self.auction_history),
            'protected_agents': len(self.evaluator.protected_agents),
            'auction_status': self.current_auction.status.value if self.current_auction else 'none'
        }

    # Extension points for future integration
    def apply_conflict_resolution(self, winners: List[AuctionWinner], 
                                conflict_actions: Dict[str, str]) -> List[AuctionWinner]:
        """Apply conflict resolution results (Nash equilibrium integration point)"""
        if not conflict_actions:
            return winners
        
        resolved_winners = []
        waiting_winners = []
        
        for winner in winners:
            action = conflict_actions.get(winner.participant.id, 'go')
            winner.conflict_action = action
            
            if action == 'go':
                resolved_winners.append(winner)
            else:
                waiting_winners.append(winner)
        
        # Reassign rankings
        all_winners = resolved_winners + waiting_winners
        for i, winner in enumerate(all_winners):
            winner.rank = i + 1
        
        if waiting_winners:
            print(f"🎮 Conflict resolution: {len(waiting_winners)} agents waiting")
        
        return all_winners
    
    # def integrate_learned_bidding_policy(self, policy_function):
    #     """Integration point for RL-based bidding policies"""
    #     # Future implementation for PPO integration
    #     pass
        
    #     if waiting_winners:
    #         print(f"🎮 Conflict resolution: {len(waiting_winners)} agents waiting")
        
    #     return all_winners
    
    def integrate_learned_bidding_policy(self, policy_function):
        """Integration point for RL-based bidding policies"""
        # Future implementation for PPO integration
        pass
