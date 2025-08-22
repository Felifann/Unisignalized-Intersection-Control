import sys
import os
import glob

base_dir = os.path.dirname(os.path.abspath(__file__))
egg_path = []

if sys.platform.startswith('linux'):
    egg_path = glob.glob(os.path.join(base_dir, "carla_l", "carla-*linux-x86_64.egg"))

if egg_path:
    sys.path.insert(0, egg_path[0])
else:
    raise RuntimeError(
        "CARLA egg not found.\n"
    )

# Import unified configuration
from config.unified_config import UnifiedConfig, get_config, print_config_summary

# ===== 环境相关模块 =====
from env.scenario_manager import ScenarioManager
from env.state_extractor import StateExtractor
from env.simulation_config import SimulationConfig

# ===== 车队管理模块 =====
from platooning.platoon_manager import PlatoonManager

# ===== 拍卖系统模块 =====
from auction.auction_engine import DecentralizedAuctionEngine

# ===== 交通控制模块 =====
from control import TrafficController

# ===== Nash deadlock solver =====
from nash.deadlock_nash_solver import DeadlockNashSolver

# Initialize unified configuration
unified_config = get_config()
print_config_summary(unified_config)

# 初始化环境模块
scenario = ScenarioManager()
state_extractor = StateExtractor(scenario.carla)

# 初始化车队管理 - 传入state_extractor用于导航
platoon_manager = PlatoonManager(state_extractor)

# ===== UNIFIED Configuration Management =====
# All parameters now managed through unified_config.py
class DRLConfig:
    """DRL parameter interface - now delegates to unified config"""
    
    @classmethod
    def update_from_drl_params(cls, **kwargs):
        """Update unified configuration from DRL training parameters"""
        unified_config.update_from_drl_params(**kwargs)
        
        # Update all system components with new config
        update_system_configuration()
        
        print(f"🤖 DRL配置更新 via UNIFIED CONFIG:")
        print(f"   Conflict window: {unified_config.conflict.conflict_time_window}s")
        print(f"   Max go agents: {'unlimited' if unified_config.mwis.max_go_agents is None else unified_config.mwis.max_go_agents}")

# 初始化分布式拍卖引擎 - 传入state_extractor
auction_engine = DecentralizedAuctionEngine(
    state_extractor=state_extractor, 
    max_go_agents=unified_config.mwis.max_go_agents
)

# 初始化Nash deadlock solver with unified config
nash_solver = DeadlockNashSolver(
    unified_config=unified_config,
    intersection_center=unified_config.system.intersection_center,
    max_go_agents=unified_config.mwis.max_go_agents
)

# 在主循环开始前添加动态配置更新
def update_system_configuration():
    """Update all system components with current unified configuration"""
    # Update Nash solver with new config
    nash_solver.update_config_params(
        conflict_time_window=unified_config.conflict.conflict_time_window,
        max_go_agents=unified_config.mwis.max_go_agents,
        min_safe_distance=unified_config.conflict.min_safe_distance,
        deadlock_speed_threshold=unified_config.deadlock.deadlock_speed_threshold
    )
    
    print(f"🔄 System configuration updated via UNIFIED CONFIG")

# 初始化交通控制器
traffic_controller = TrafficController(scenario.carla, state_extractor, max_go_agents=unified_config.mwis.max_go_agents)

# REACTIVATED: Set platoon manager reference
traffic_controller.set_platoon_manager(platoon_manager)

# Connect Nash solver to auction engine
auction_engine.set_nash_controller(nash_solver)

# 显示地图信息
spawn_points = scenario.carla.world.get_map().get_spawn_points()
print(f"=== 无信号灯交叉路口仿真 (集成拍卖系统) ===")

# 生成交通流
scenario.reset_scenario()
scenario.start_time_counters()  # <-- start real/sim timers immediately after reset
scenario.show_intersection_area()      # Show larger general intersection area
scenario.show_intersection_area1()     # Show smaller core deadlock detection area

print("🔍 死锁检测区域：使用小型核心区域 (蓝色边框)")
print("🚦 一般拍卖区域：使用大型检测区域 (绿色边框)")

# 在仿真开始前添加
from traffic_light_override import force_vehicles_run_lights, freeze_lights_green

# 选择一种方法
# force_vehicles_run_lights(scenario.carla.world, scenario.carla.traffic_manager)
# 或者
# freeze_lights_green(scenario.carla.world)

# 主仿真循环
try:
    step = 0
    unified_update_interval = 10
    unified_print_interval = SimulationConfig.PRINT_INTERVAL
    
    while True:
        scenario.carla.world.tick()
        vehicle_states = state_extractor.get_vehicle_states()
        
        if step % unified_update_interval == 0:
            try:
                # Optional: Check for configuration updates every few cycles
                if step % (unified_update_interval * 10) == 0:  # Every 100 steps
                    update_system_configuration()
                
                # 1. 更新车队分组
                platoon_manager.update()
                
                # 2. 更新拍卖系统
                auction_winners = auction_engine.update(vehicle_states, platoon_manager)

                # 3. 更新交通控制 - Pass winners directly
                traffic_controller.update_control(platoon_manager, auction_engine, auction_winners)
                
            except Exception as e:
                if "deadlock" in str(e).lower():
                    print(f"\n🚨 Deadlock detected: {e}")
                    print("🛑 Stopping simulation due to deadlock...")
                    break
                else:
                    print(f"⚠️  Error in simulation update: {e}")
                    # Continue simulation for other errors
        
        # 统一打印频率：所有状态信息同时输出
        if step % unified_print_interval == 0:
            # 清屏（可选，让输出更清晰）
            os.system('clear')  # Linux: use 'clear' to clear the terminal
            
            print(f"\n{'='*80}")
            print(f"[Step {step}] 无信号灯交叉路口仿真状态报告")
            print(f"{'='*80}")
            
            # 基础仿真信息
            actual_fps = 1 / SimulationConfig.FIXED_DELTA_SECONDS
            vehicles_in_radius = vehicle_states
            vehicles_in_junction = [v for v in vehicle_states if v['is_junction']]
            
            print(f"📊 基础信息: FPS:{actual_fps:.1f}, 车辆总数:{len(vehicles_in_radius)}, 路口内:{len(vehicles_in_junction)}")
            print(f"🎮 系统配置: NO GO LIMIT, CONFLICT_WINDOW={DRLConfig.CONFLICT_TIME_WINDOW}s")
            
            # 1. 车队管理状态
            # platoon_manager.print_platoon_info()
            
            # ENHANCED: Show detailed platoon coordination status
            platoons = platoon_manager.get_all_platoons()
            if platoons:
                print(f"\n🔍 车队协调状态:")
                for platoon in platoons[:4]:  # Show top 3 platoons
                    leader_id = platoon.get_leader_id()
                    follower_ids = platoon.get_follower_ids()
                    
                    # Check if platoon vehicles are under control
                    controlled_count = 0
                    total_vehicles = platoon.get_size()
                    
                    control_stats = traffic_controller.get_control_stats()
                    controlled_vehicle_ids = set(control_stats.get('active_controls', []))
                    
                    platoon_vehicle_ids = platoon.get_vehicle_ids()
                    for vid in platoon_vehicle_ids:
                        if vid in controlled_vehicle_ids:
                            controlled_count += 1
                    
                    coordination_status = "🟢" if controlled_count == total_vehicles else "🟡" if controlled_count > 0 else "🔴"
                    
                    print(f"   {coordination_status} {platoon.platoon_id}: "
                          f"{controlled_count}/{total_vehicles} 受控 "
                          f"(L:{leader_id}, F:{len(follower_ids)})")

            # 2. 拍卖系统状态 - ENHANCED WITH CONFLICT INFO
            print(f"\n🎯 拍卖系统状态:")
            
            # 显示当前优先级排序（前5名）
            priority_order = auction_engine.get_current_priority_order()
            if priority_order:
                go_count = sum(1 for w in priority_order if w.conflict_action == 'go')
                wait_count = sum(1 for w in priority_order if w.conflict_action == 'wait')
                print(f"   📋 当前决策: {go_count} GO, {wait_count} WAIT (no limit)")
                print(f"   🏆 当前通行优先级（前5名）:")
                for winner in priority_order[:5]:
                    participant = winner.participant
                    bid_value = winner.bid.value
                    rank = winner.rank
                    conflict_action = winner.conflict_action
                    action_emoji = "🟢" if conflict_action == 'go' else "🔴"
                    
                    # ENHANCED: Show both vehicle and platoon info
                    if participant.type == 'vehicle':
                        print(f"      #{rank}: {action_emoji}🚗车辆{participant.id} "
                              f"出价:{bid_value:.1f}")
                    elif participant.type == 'platoon':
                        vehicle_count = len(participant.vehicles)
                        print(f"      #{rank}: {action_emoji}🚛车队{participant.id} "
                              f"({vehicle_count}车) 出价:{bid_value:.1f}")
            
            # 3. 控制器状态 - ENHANCED WITH EXIT TRACKING
            control_stats = traffic_controller.get_control_stats()
            if control_stats['total_controlled'] > 0:
                platoon_info = f"车队成员:{control_stats['platoon_members']}, 领队:{control_stats['platoon_leaders']}" if control_stats['platoon_members'] > 0 else ""
                print(f"🎮 控制器状态: 当前控制:{control_stats['total_controlled']} | "
                      f"等待:{control_stats['waiting_vehicles']} | "
                      f"通行:{control_stats['go_vehicles']} | {platoon_info}")
                print(f"   📊 统计: 总控制车辆:{control_stats['total_vehicles_ever_controlled']} | "
                      f"已离开路口:{control_stats['vehicles_exited_intersection']}")
            
            # 4. 拍卖系统统计 - ENHANCED
            auction_stats = auction_engine.get_auction_stats()
            if auction_stats['current_agents'] > 0:
                print(f"🎯 拍卖统计: 参与者:{auction_stats['current_agents']} "
                      f"(车队:{auction_stats['platoon_agents']}, 单车:{auction_stats['vehicle_agents']})")
                print(f"   状态: {auction_stats['auction_status']}, "
                      f"GO决策: {auction_stats['current_go_count']} (no limit)")

        # 更新车辆ID标签显示（保持原频率）
        scenario.update_vehicle_labels()
        
        step += 1

except KeyboardInterrupt:
    print("\n仿真已手动终止。")
except Exception as e:
    if "deadlock" in str(e).lower():
        print(f"\n🚨 仿真因死锁而终止: {e}")
    else:
        print(f"\n❌ 仿真意外终止: {e}")
finally:
    # Stop timers and print elapsed times before exiting
    try:
        scenario.stop_time_counters()
        real_elapsed = scenario.get_real_elapsed()
        sim_elapsed = scenario.get_sim_elapsed()
        print("\n⏱ 仿真时间统计:")
        print(f"   • 实时耗时 (wall-clock): {scenario.format_elapsed(real_elapsed)} ({real_elapsed:.2f}s)")
        print(f"   • 仿真世界时间    : {scenario.format_elapsed(sim_elapsed)} "
              f"({sim_elapsed:.2f}s)" if sim_elapsed is not None else "   • 仿真世界时间    : N/A")
    except Exception as e:
        print(f"⚠️ 无法获取时间统计: {e}")

    # Print traffic control statistics
    try:
        control_final_stats = traffic_controller.get_final_statistics()
        print("\n🎮 交通控制统计:")
        print(f"   • 总控制车辆数: {control_final_stats['total_vehicles_controlled']}")
        print(f"   • 成功离开路口: {control_final_stats['vehicles_exited_intersection']}")
        print(f"   • 仍在控制中: {control_final_stats['vehicles_still_controlled']}")
        print(f"   • 控制历史记录: {control_final_stats['control_history_count']}")
        
        # New: Print enhanced acceleration statistics
        avg_pos_accel = control_final_stats['average_positive_acceleration']
        avg_neg_accel = control_final_stats['average_negative_acceleration']
        avg_abs_accel = control_final_stats['average_absolute_acceleration']
        
        # NEW: Print separate absolute averages for positive/negative accelerations
        avg_abs_pos_accel = control_final_stats.get('average_absolute_positive_acceleration', 0.0)
        avg_abs_neg_accel = control_final_stats.get('average_absolute_negative_acceleration', 0.0)
        
        pos_samples = control_final_stats['positive_acceleration_samples']
        neg_samples = control_final_stats['negative_acceleration_samples']
        abs_samples = control_final_stats['absolute_acceleration_samples']
        
        pos_vehicles = control_final_stats['positive_acceleration_vehicles']
        neg_vehicles = control_final_stats['negative_acceleration_vehicles']
        abs_vehicles = control_final_stats['absolute_acceleration_vehicles']
        
        print(f"   • 平均正加速度: {avg_pos_accel:.3f} m/s² (绝对值: {avg_abs_pos_accel:.3f} m/s²) ({pos_samples} 样本, {pos_vehicles} 车辆)")
        print(f"   • 平均负加速度: {avg_neg_accel:.3f} m/s² (绝对值: {avg_abs_neg_accel:.3f} m/s²) ({neg_samples} 样本, {neg_vehicles} 车辆)")
        print(f"   • 平均绝对加速度: {avg_abs_accel:.3f} m/s² ({abs_samples} 样本, {abs_vehicles} 车辆)")
    
        # Print 单位时间通行数
        throughput = control_final_stats['vehicles_exited_intersection'] / sim_elapsed * 3600 if sim_elapsed > 0 else 0
        print(f"   • 单位时间通行数: {throughput:.1f} 辆/h")
        
    except Exception as e:
        print(f"⚠️ 无法获取控制统计: {e}")

    # Print collision report (only printed at simulation end)
    try:
        scenario.traffic_generator.print_collision_report()
    except Exception as e:
        print(f"⚠️ 无法获取碰撞统计: {e}")

    print("\n🏁 仿真结束")


