import numpy as np
import time
from typing import Dict, List, Tuple, Any, Optional
from collections import deque

class SimulationMetricsManager:
    """Dedicated manager for simulation metrics tracking and validation"""
    
    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        
        # Core metrics
        self.metrics = {
            'avg_acceleration': 0.0,
            'collision_count': 0,
            'prev_vehicles_exited': 0,
            'prev_collision_count': 0,
            'using_real_data': True,
            'throughput': 0.0,
            'last_throughput_calc_step': -1
        }
        
        # Performance tracking with memory management
        self.perf_stats = {
            'step_times': deque(maxlen=200),
            'obs_times': deque(maxlen=200),
            'reward_times': deque(maxlen=200),
            'total_ticks': 0
        }
        
        # Reward validation
        self.reward_history = deque(maxlen=100)
        self.suspicious_rewards = 0
        
        print("📊 Metrics Manager initialized with memory-bounded tracking")

    def reset_metrics(self):
        """Reset all metrics for new episode"""
        self.metrics = {
            'avg_acceleration': 0.0,
            'collision_count': 0,
            'prev_vehicles_exited': 0,
            'prev_collision_count': 0,
            'using_real_data': True,
            'throughput': 0.0,
            'last_throughput_calc_step': -1
        }
        
        # Clear performance stats but keep structure
        for key in self.perf_stats:
            if key != 'total_ticks':
                self.perf_stats[key].clear()
        
        self.reward_history.clear()
        self.suspicious_rewards = 0
        
        print("🔄 Metrics reset with memory cleanup")

    def calculate_reward(self, traffic_controller, state_extractor, scenario, 
                        nash_solver, current_step: int) -> float:
        """Calculate validated reward from real simulation data"""
        reward = 0.0
        
        try:
            # Get REAL statistics
            control_stats = traffic_controller.get_control_stats()
            final_stats = traffic_controller.get_final_statistics()
            
            # VALIDATED exit reward - prevent phantom exits
            current_exited = final_stats.get('vehicles_exited_intersection', 0)
            prev_exited = self.metrics.get('prev_vehicles_exited', 0)
            new_exits = max(0, current_exited - prev_exited)
            
            # Cross-validation with current vehicle count
            current_vehicles = state_extractor.get_vehicle_states()
            current_vehicle_count = len(current_vehicles)
            
            if new_exits > 0:
                # Validate exits are realistic
                if new_exits > current_vehicle_count + 3:
                    print(f"⚠️ SUSPICIOUS: {new_exits} exits vs {current_vehicle_count} vehicles")
                    new_exits = 0  # Reject suspicious exits
                else:
                    reward += new_exits * 5.0
                    if new_exits > 0:
                        print(f"✅ +{new_exits * 5.0} for {new_exits} verified exits")
            
            self.metrics['prev_vehicles_exited'] = current_exited
            
            # Acceleration penalty (periodic update)
            if current_step % 20 == 0:
                real_avg_accel = final_stats.get('average_absolute_acceleration', 0.0)
                self.metrics['avg_acceleration'] = real_avg_accel
                
                if real_avg_accel > 2.0:
                    reward -= (real_avg_accel - 2.0) * 1.0
            
            # Control effectiveness reward
            if current_vehicles:
                controlled_vehicles = control_stats.get('total_controlled', 0)
                control_ratio = controlled_vehicles / len(current_vehicles)
                reward += control_ratio * 1.0
            
            # Collision penalty
            if hasattr(scenario, 'traffic_generator') and hasattr(scenario.traffic_generator, 'collision_count'):
                current_collisions = scenario.traffic_generator.collision_count
                prev_collisions = self.metrics.get('prev_collision_count', 0)
                new_collisions = current_collisions - prev_collisions
                
                if new_collisions > 0:
                    reward -= new_collisions * 20.0
                    self.metrics['prev_collision_count'] = current_collisions
            
            # Small penalties and bonuses
            reward -= 0.01  # Step penalty
            
            if current_vehicle_count > 2 and control_stats.get('total_controlled', 0) > 0:
                reward += 0.1  # Activity bonus
            
            # Bound reward to realistic range
            reward = np.clip(reward, -50.0, 50.0)
            
            # Track for validation
            self.reward_history.append(reward)
            if abs(reward) > 30.0:
                self.suspicious_rewards += 1
            
            return reward
            
        except Exception as e:
            print(f"❌ Reward calculation failed: {e}")
            return -1.0

    def calculate_throughput(self, scenario, current_step: int, 
                           vehicles_exited: int) -> float:
        """Calculate throughput with caching and validation"""
        try:
            recalc_interval = 50
            should_recalc = (current_step % recalc_interval == 0) or (
                self.metrics.get('last_throughput_calc_step', -1) < 0)
            
            if should_recalc:
                sim_elapsed = (scenario.get_sim_elapsed() 
                             if hasattr(scenario, 'get_sim_elapsed') else None)
                
                if sim_elapsed is not None and sim_elapsed > 0.1 and vehicles_exited >= 0:
                    computed = (vehicles_exited / sim_elapsed) * 3600.0
                    real_throughput = max(0.0, min(float(computed), 3600.0))
                    
                    self.metrics['throughput'] = real_throughput
                    self.metrics['last_throughput_calc_step'] = current_step
                    
                    if real_throughput > 2000:
                        print(f"⚠️ High throughput: {real_throughput:.1f} v/h")
                    
                    return real_throughput
            
            # Return cached value
            return float(self.metrics.get('throughput', 0.0))
            
        except Exception as e:
            print(f"⚠️ Throughput calculation error: {e}")
            return float(self.metrics.get('throughput', 0.0))

    def get_info_dict(self, traffic_controller, auction_engine, nash_solver,
                     scenario, state_extractor, bid_policy, current_step: int,
                     max_steps: int) -> Dict:
        """Generate comprehensive info dictionary with validation"""
        try:
            # Get real statistics
            control_stats = traffic_controller.get_control_stats()
            final_stats = traffic_controller.get_final_statistics()
            auction_stats = auction_engine.get_auction_stats()
            current_vehicles = state_extractor.get_vehicle_states()
            
            # Calculate throughput
            vehicles_exited = final_stats['vehicles_exited_intersection']
            real_throughput = self.calculate_throughput(scenario, current_step, vehicles_exited)
            
            # Basic validation
            sim_elapsed = (scenario.get_sim_elapsed() 
                         if hasattr(scenario, 'get_sim_elapsed') else None)
            data_valid = sim_elapsed is not None and vehicles_exited >= 0
            
            # Get collision and deadlock data
            collision_count = 0
            if (hasattr(scenario, 'traffic_generator') and 
                hasattr(scenario.traffic_generator, 'collision_count')):
                collision_count = scenario.traffic_generator.collision_count
            
            deadlocks_detected = 0
            if hasattr(nash_solver, 'stats'):
                deadlocks_detected = nash_solver.stats.get('deadlocks_detected', 0)
            
            return {
                # Core simulation metrics
                'throughput': float(real_throughput),
                'avg_acceleration': float(final_stats.get('average_absolute_acceleration', 0.0)),
                'collision_count': int(collision_count),
                'total_controlled': int(final_stats['total_vehicles_controlled']),
                'vehicles_exited': int(vehicles_exited),
                'auction_agents': int(auction_stats.get('current_agents', 0)),
                'deadlocks_detected': int(deadlocks_detected),
                
                # Real-time state
                'vehicles_detected': len(current_vehicles),
                'vehicles_in_junction': sum(1 for v in current_vehicles 
                                          if v.get('is_junction', False)),
                'go_vehicles': int(control_stats.get('go_vehicles', 0)),
                'waiting_vehicles': int(control_stats.get('waiting_vehicles', 0)),
                
                # Training parameters
                'bid_scale': float(bid_policy.bid_scale),
                'eta_weight': float(bid_policy.eta_weight),
                'speed_weight': float(bid_policy.speed_weight),
                'congestion_sensitivity': float(bid_policy.congestion_sensitivity),
                'platoon_bonus': float(bid_policy.platoon_bonus),
                'junction_penalty': float(bid_policy.junction_penalty),
                'fairness_factor': float(bid_policy.fairness_factor),
                'urgency_threshold': float(bid_policy.urgency_threshold),
                'proximity_bonus_weight': float(bid_policy.proximity_bonus_weight),
                'speed_diff_modifier': float(bid_policy.speed_diff_modifier),
                'follow_distance_modifier': float(bid_policy.follow_distance_modifier),
                'ignore_vehicles_go': float(bid_policy.ignore_vehicles_go),
                'ignore_vehicles_wait': float(bid_policy.ignore_vehicles_wait),
                'ignore_vehicles_platoon_leader': float(bid_policy.ignore_vehicles_platoon_leader),
                'ignore_vehicles_platoon_follower': float(bid_policy.ignore_vehicles_platoon_follower),
                
                # Metadata
                'sim_time_elapsed': float(sim_elapsed) if sim_elapsed is not None else 0.0,
                'current_step': int(current_step),
                'max_steps': int(max_steps),
                'using_real_data': True,
                'data_source': 'actual_carla_simulation',
                'data_validated': data_valid,
                
                # Quality metrics
                'reward_validation': {
                    'suspicious_rewards': self.suspicious_rewards,
                    'avg_recent_reward': np.mean(list(self.reward_history)) if self.reward_history else 0.0,
                    'reward_stability': np.std(list(self.reward_history)) if len(self.reward_history) > 1 else 0.0
                }
            }
            
        except Exception as e:
            print(f"❌ Failed to get info: {e}")
            return {
                'using_real_data': False,
                'data_source': 'error_fallback',
                'error': str(e)
            }

    def record_performance(self, step_time: float, obs_time: float = None, 
                          reward_time: float = None):
        """Record performance metrics"""
        self.perf_stats['step_times'].append(step_time)
        if obs_time is not None:
            self.perf_stats['obs_times'].append(obs_time)
        if reward_time is not None:
            self.perf_stats['reward_times'].append(reward_time)
        self.perf_stats['total_ticks'] += 1

    def get_performance_stats(self) -> Dict:
        """Get performance statistics"""
        if not self.perf_stats['step_times']:
            return {}
        
        return {
            'avg_step_time': np.mean(list(self.perf_stats['step_times'])),
            'min_step_time': np.min(list(self.perf_stats['step_times'])),
            'max_step_time': np.max(list(self.perf_stats['step_times'])),
            'avg_obs_time': np.mean(list(self.perf_stats['obs_times'])) if self.perf_stats['obs_times'] else 0,
            'total_ticks': self.perf_stats['total_ticks'],
            'memory_usage': {
                'step_times_len': len(self.perf_stats['step_times']),
                'obs_times_len': len(self.perf_stats['obs_times']),
                'reward_history_len': len(self.reward_history)
            }
        }
