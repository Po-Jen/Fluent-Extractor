#!/usr/bin/env python
import cv2
import numpy as np
import csv
import math
import rospy
import os
import tuck_arms
import joint_trajectory_file_playback
from std_msgs.msg import (
    UInt16,
)

import baxter_interface
from baxter_interface import (
    DigitalIO
)

from std_msgs.msg import String
from std_msgs.msg import Header
import struct
from baxter_interface import CHECK_VERSION
import baxter_external_devices
from geometry_msgs.msg import (
    PoseStamped,
    Pose,
    Point,
    Quaternion,
)

from baxter_core_msgs.srv import (
    SolvePositionIK,
    SolvePositionIKRequest,
)

def dist_pos(pos_a, pos_b):
    return ((pos_a[0] - pos_b[0])**2 + (pos_a[1] - pos_b[1])**2 + (pos_a[2] - pos_b[2])**2) ** 0.5

grip_quat_near = Quaternion(x=0.003, y=0.913, z=0.03, w=0.26)
grip_quat_far = Quaternion(x=-0.015, y=0.946, z=-0.0122, w=0.323)

grip_quat_near_right = Quaternion(x=-0.166755840964, y=0.984408007754, z=0.0474837929061, w=0.0296420847001)
grip_quat_near_left = Quaternion(x=0.138610321149, y=0.989974768164, z=0.00934995996438, w=0.0254895177964)
grip_quat_diag1_left = Quaternion(x=-0.0272329635807, y=0.939143659652, z=-0.330272832324, w=0.0904842995141)
grip_quat_diag1_right = Quaternion(x=0.00388033002546, y=0.939712736087, z=0.335003571924, w=0.0681347991566)
grip_quat_far_right = Quaternion(x=-0.0951612807212, y=0.93509826081, z=0.0220377798776, w=0.340660989165)
grip_quat_far_left = Quaternion(x=-0.00836081491788, y=0.936349173177, z=-0.0590992233266, w=0.34595896356)

grip_quat_far_sleeve_left = Quaternion(x=0.159039527024, y=0.966563204639, z=0.0260476525512, w=0.199458065965)
grip_quat_far_sleeve_right = Quaternion(x=-0.210220991353, y=0.958000746332, z=-0.0262295833013, w=0.193271088839)

grip_quat_raise_left = Quaternion(x=0.0390104192206, y=0.885870078652, z=-0.462257511788, w=-0.00551214451998)
grip_quat_raise_right = Quaternion(x=-0.00493548757261, y=0.895511255249, z=0.442648807361, w=0.0457959171328)

grip_quat_far2_left = Quaternion(x=0.0858671666539, y=0.935089288861, z=-0.0618287152105, w=0.338248520357)
grip_quat_far2_right = Quaternion(x=-0.149847636437, y=0.932923007439, z=0.0233373030367, w=0.326581870795)

grip_quat_flatten_left = Quaternion(x=0.679248526695, y=0.0555814849351, z=0.0481083594103, w=0.73021758625)
grip_quat_flatten_left2 = Quaternion(x=0.74734025648, y=-0.0349752551918, z=0.0903587461151, w=0.657339006579)



class ClothFolder(object):

    def __init__(self, single_arm_mode=None):
        self._single_arm_mode = single_arm_mode
        self._pub_rate = rospy.Publisher('robot/joint_state_publish_rate', UInt16, queue_size=10)
        self._chat_pub = rospy.Publisher('/robot_stc/client/chat', String, queue_size=1000)
        self._left_arm = baxter_interface.limb.Limb("left")
        self._right_arm = baxter_interface.limb.Limb("right")
        self._left_joint_names = self._left_arm.joint_names()
        self._right_joint_names = self._right_arm.joint_names()

        self._close_io = DigitalIO('%s_upper_button' % ('right',))  # 'dash' btn
        self._open_io = DigitalIO('%s_lower_button' % ('right',))   # 'circle' btn
        self._light_io = DigitalIO('%s_lower_cuff' % ('right',))    # cuff squeeze
        self._open_io.state_changed.connect(self._open_action)
        self._close_io.state_changed.connect(self._close_action)

        self._rate = 50.  # Hz

        self._rs = baxter_interface.RobotEnable(CHECK_VERSION)
        self._init_state = self._rs.state().enabled
        print("Enabling robot... ")
        self._rs.enable()

        self._pub_rate.publish(self._rate)
        if self._single_arm_mode == 'left':
            self.calibrate_left_grip()
        elif self._single_arm_mode == 'right':
            self.calibrate_right_grip()
        else:
            self.calibrate_grips()

    # cuff (circle)
    def _open_action(self, value):
        if value:
            print("gripper open triggered")
            self._chat_pub.publish("look")

    # cuff (dash)
    def _close_action(self, value):
        if value:
            print("gripper close triggered")
            self._chat_pub.publish("learn")

    def rest(self, limb):
        if limb == 'left':
            self.move_arm('left', 0.6, 0.6, 0.35, grip_quat_near_left)
        elif limb == 'right':
            self.move_arm('right', 0.6, -0.6, 0.35, grip_quat_near_right)

    def move_arms(self, l, r, l_q=None, r_q=None):
        duration = 1.
        sleep_rate = 0.005
        sleep_counts = duration / sleep_rate
        l_pos = self._left_arm.endpoint_pose()['position']
        l_pos_arr = [l_pos.x, l_pos.y, l_pos.z]
        l_ik = self.approx_compute_ik('left', l_pos_arr, l, l_q)
        r_pos = self._right_arm.endpoint_pose()['position']
        r_pos_arr = [r_pos.x, r_pos.y, r_pos.z]
        r_ik = self.approx_compute_ik('right', r_pos, r, r_q)
        if (not l_ik) and (not r_ik):
            print ('moving both arms this way is impossible')
            return
        elif not l_ik:
            print('left ik impossible')
            return
        elif not r_ik:
            print('right ik impossible')
            return
        left_sleep_count = 0
        right_sleep_count = 0
        left_done = False
        right_done = False
        while (not rospy.is_shutdown()) and (not left_done or not right_done):
            self._right_arm.set_joint_positions(r_ik)
            self._left_arm.set_joint_positions(l_ik)
            should_sleep = False
            if ('position' in self._right_arm.endpoint_pose()):
                pos = self._right_arm.endpoint_pose()['position']
                dist = ( (pos.x - r[0])**2 +
                         (pos.y - r[1])**2 +
                         (pos.z - r[2])**2 ) ** 0.5
                should_sleep = True
                rospy.sleep(sleep_rate)
                right_sleep_count += 1
                if dist < 0.02 or right_sleep_count > sleep_counts:
                    right_done = True

            else:
                break
            if ('position' in self._left_arm.endpoint_pose()):
                pos = self._left_arm.endpoint_pose()['position']
                dist = ( (pos.x - l[0])**2 +
                         (pos.y - l[1])**2 +
                         (pos.z - l[2])**2 ) ** 0.5
                should_sleep = True
                rospy.sleep(sleep_rate)
                left_sleep_count += 1
                if dist < 0.02 or left_sleep_count > sleep_counts:
                    left_done = True

            else:
                break



    def move_arms_old(self, grip1, grip2):
        pos_right = self._right_arm.endpoint_pose()['position']
        pos_left = self._left_arm.endpoint_pose()['position']
        g1_dist_to_r = ((grip1[0] - pos_right.x)**2 + (grip1[1] - pos_right.y)**2 + (grip1[2] - pos_right.z)**2) ** 0.5
        g1_dist_to_l = ((grip1[0] - pos_left.x)**2 + (grip1[1] - pos_left.y)**2 + (grip1[2] - pos_left.z)**2) ** 0.5
        if g1_dist_to_l < g1_dist_to_r:
            left_grip = grip1
            right_grip = grip2
        else:
            left_grip = grip2
            right_grip = grip1
        print('moving left arm to ' + str(left_grip))
        print('moving right arm to ' + str(right_grip))
        if left_grip[0] < 0.68:
            grip_quat = grip_quat_near
        else:
            grip_quat = grip_quat_far
        left_ik = self.compute_ik('left', left_grip, grip_quat)
        if right_grip[0] < 0.68:
            grip_quat = grip_quat_near
        else:
            grip_quat = grip_quat_far
        right_ik = self.compute_ik('right', right_grip, grip_quat)
        if left_ik and right_ik:
            left_done = False
            right_done = False
            sleep_count = 0
            while (not rospy.is_shutdown()) and (not left_done or not right_done):
                self._right_arm.set_joint_positions(right_ik)
                self._left_arm.set_joint_positions(left_ik)
                if ('position' in self._right_arm.endpoint_pose()):
                    pos = self._right_arm.endpoint_pose()['position']
                    dist = ( (pos.x - right_grip[0])**2 +
                             (pos.y - right_grip[1])**2 +
                             (pos.z - right_grip[2])**2 ) ** 0.5
                    print('r', sleep_count, dist)
                    rospy.sleep(0.12)
                    sleep_count += 1
                    if dist < 0.02 or sleep_count > 60:
                        right_done = True
                else:
                    break
                if ('position' in self._left_arm.endpoint_pose()):
                    pos = self._left_arm.endpoint_pose()['position']
                    dist = ( (pos.x - left_grip[0])**2 +
                             (pos.y - left_grip[1])**2 +
                             (pos.z - left_grip[2])**2 ) ** 0.5
                    print('l', sleep_count, dist)
                    rospy.sleep(0.1)
                    sleep_count += 1
                    if dist < 0.02 or sleep_count > 60:
                        left_done = True
                else:
                    break

            print("joint position set")
        if not left_ik:
            print("left arm can't reach")
        if not right_ik:
            print("right arm can't reach")

    def calibrate(self):
        self.close_grip('right')
        z_val = 0.045
#        calibrate_locs = [
#            [0.47, -0.2, z_val], [0.57, -0.2, z_val], [0.67, -0.2, z_val],
#            [0.47, -0.1, z_val], [0.57, -0.1, z_val], [0.67, -0.1, z_val],
#            [0.47, 0.0, z_val], [0.57, 0.0, z_val], [0.67, 0.0, z_val],
#            [0.47, 0.1, z_val], [0.57, 0.1, z_val], [0.67, 0.1, z_val],
#            [0.47, 0.2, z_val], [0.57, 0.2, z_val], [0.67, 0.2, z_val]
#        ]

        xs = [0.5, 0.7]
        ys = [-0.3, -0.15]

        calibrate_locs = []
        for x in xs:
            for y in ys:
                calibrate_locs.append([x, y, z_val + 0.04])
                calibrate_locs.append([x, y, z_val])
                calibrate_locs.append([x, y, z_val + 0.04])

        done = False
        calibrate_idx = 0;
        print('press n')
        while not done and not rospy.is_shutdown():
            c = baxter_external_devices.getch()
            if c:
            #catch Esc or ctrl-c
                if c in ['\x1b', '\x03']:
                    done = True
                    rospy.signal_shutdown("Example finished.")
                else:
                    print("key: ", c)
                    if c == 'n':
#                        if c_x:
#                            self.move_arm('right', c_x, c_y, c_z+0.1, grip_quat_far)
                        if calibrate_idx < len(calibrate_locs):
                            c_x = calibrate_locs[calibrate_idx][0]
                            c_y = calibrate_locs[calibrate_idx][1]
                            c_z = calibrate_locs[calibrate_idx][2]
                            self.move_arm('right', c_x, c_y, c_z, grip_quat_near_right)
                            calibrate_idx += 1
                        else:
                            print('done')
                    if c == 'u':
                        self.move_arm('right', c_x*0.95, c_y*0.95, c_z+0.02, grip_quat_near_right)

    def approx_compute_ik(self, limb, a, b, q=None):
        if limb == 'right':
            arm = self._right_arm
            quat = grip_quat_near_right
        elif limb == 'left':
            arm = self._left_arm
            quat = grip_quat_near_left
        else:
            print('invalid arm in `approx_compute_ik`')
            return
        mid = [(a[0]+b[0])/2.0, (a[1]+b[1])/2.0, (a[2]+b[2])/2.0]

        if mid[0] > 0.6:
            if limb == 'right':
                quat = grip_quat_far_sleeve_right
#                quat = grip_quat_far2_right
            else:
                quat = grip_quat_far_sleeve_left
#                quat = grip_quat_far2_left
        if mid[2] > 0.2:
            if limb == 'right':
                quat = grip_quat_diag1_right
            else:
                quat = grip_quat_diag1_left

        if q is not None:
            quat = q

        mid_ik = self.compute_ik(limb, mid, quat)
        if dist_pos(a,b) < 0.02:
            return mid_ik
        elif mid_ik:
            return self.approx_compute_ik(limb, mid, b)
        else:
            return self.approx_compute_ik(limb, a, mid)


    def compute_ik(self, limb, pos, quat):
        ns = "ExternalTools/" + limb + "/PositionKinematicsNode/IKService"
        iksvc = rospy.ServiceProxy(ns, SolvePositionIK)
        ikreq = SolvePositionIKRequest()
        hdr = Header(stamp=rospy.Time.now(), frame_id='base')
        goal_pose = PoseStamped(
            header=hdr,
            pose=Pose(
                position=Point(
                    x=pos[0],
                    y=pos[1],
                    z=pos[2],
                ),
            orientation=quat,
            ),
        )
        ikreq.pose_stamp.append(goal_pose)
        try:
            rospy.wait_for_service(ns, 5.0)
            resp = iksvc(ikreq)
        except (rospy.ServiceException, rospy.ROSException), e:
            rospy.logerr("Service call failed: %s" % (e,))
            return None

        resp_seeds = struct.unpack('<%dB' % len(resp.result_type),
                                       resp.result_type)
        if (resp_seeds[0] != resp.RESULT_INVALID):
            limb_joints = dict(zip(resp.joints[0].name, resp.joints[0].position))
            return limb_joints
        else:
            print("INVALID POSE - No Valid Joint Solution Found.")
            return None

    def move_arm_helper(self, limb, pos_a, pos_b, pos_g):
        if dist_pos(pos_a, pos_g) < 0.02:
            print('move_arm goal reached')
            return
        if dist_pos(pos_a, pos_b) < 0.02:
            print('move_arm goal unreachable, tried as far as possible')
            return
        if pos_a[0] < 0.68:
            quat = grip_quat_near
        else:
            quat = grip_quat_far
        ik_sol = self.compute_ik(limb, pos_b, quat)
        if ik_sol:
            if limb == 'right':
                arm = self._right_arm
            elif limb == 'left':
                arm = self._left_arm
            sleep_count = 0
            while not rospy.is_shutdown():
                arm.set_joint_positions(ik_sol)
                if ('position' in arm.endpoint_pose()):
                    pos = arm.endpoint_pose()['position']
                    dist = ( (pos.x - pos_b[0])**2 +
                             (pos.y - pos_b[1])**2 +
                             (pos.z - pos_b[2])**2 ) ** 0.5
                    rospy.sleep(0.1)
                    sleep_count += 1
                    if dist < 0.02 or sleep_count > 40:
                        break
                else:
                    break
            pos = arm.endpoint_pose()['position']
            self.move_arm_helper(limb, pos, pos_g, pos_g)
        else:
            mid = [ (pos_a[0] + pos_b[0])/2.0, (pos_a[1] + pos_b[1])/2.0, (pos_a[2] + pos_b[2])/2.0 ]
            self.move_arm_helper(limb, pos_a, mid, pos_g)

    def move_arm(self, limb, goal_x, goal_y, goal_z, quat, verify = False):
        if limb == 'right':
            arm = self._right_arm
        elif limb == 'left':
            arm = self._left_arm
        pos = arm.endpoint_pose()['position']
        goal = [goal_x, goal_y, goal_z]
        ik = None # self.approx_compute_ik(limb, [pos.x, pos.y, pos.z], goal)
        if not ik:
            ik = self.compute_ik(limb, goal, quat)
        if not ik:
            print('cant move arm this way')
            return

        pos = arm.endpoint_pose()['position']
        orig_pos = [pos.x, pos.y, pos.z]

        sleep_count = 0
        reached_middle = False
        while (not rospy.is_shutdown()):
            arm.set_joint_positions(ik)
            if ('position' in arm.endpoint_pose()):
                pos = arm.endpoint_pose()['position']
                dist = ( (pos.x - goal[0])**2 +
                         (pos.y - goal[1])**2 +
                         (pos.z - goal[2])**2 ) ** 0.5
                dist0 = ( (pos.x - orig_pos[0])**2 +
                          (pos.y - orig_pos[1])**2 +
                          (pos.z - orig_pos[2])**2 ) ** 0.5
                if dist0 > 3*dist and not reached_middle and verify:
                    print("at middle of move_arm action")
                    verify_published = False
                    while not rospy.is_shutdown() and not verify_published:
                        self._chat_pub.publish("verify 50")
                        print('publishing "verify 50"')
                        rospy.sleep(0.1)
                        verify_published = True
                    reached_middle = True
                rospy.sleep(0.2)
                sleep_count += 1
                if dist < 0.01 or sleep_count > 50:
                    break
            else:
                break

    def clean_shutdown(self):
        print("\nExiting example...")
        #return to normal
        self.rest('right')
        self.rest('left')
        if not self._init_state:
            print("Disabling robot...")
            self._rs.disable()
            return True

    def fold1(self, limb, grip1, release1):
        self.rest('right')
        self.move_arm(limb, grip1[0], grip1[1], grip1[2])

    def close_grip(self, limb):
        grip_right = baxter_interface.Gripper(limb, CHECK_VERSION)
        sleep_count = 0
        while not rospy.is_shutdown():
            grip_right.close()
            rospy.sleep(0.01)
            sleep_count += 1
            if sleep_count > 100:
                break

    # angle [-1.5, 1.5]
    def rotate_head(self, angle):
        head = baxter_interface.Head()
        start = rospy.get_time()
        command_rate = rospy.Rate(1)
        control_rate = rospy.Rate(100)
        while not rospy.is_shutdown() and (rospy.get_time() - start < 1.0):
            while (not rospy.is_shutdown() and
                   not (abs(head.pan() - angle) <=
                    baxter_interface.HEAD_PAN_ANGLE_TOLERANCE)):
                head.set_pan(angle, speed=30, timeout=0)
                control_rate.sleep()
            command_rate.sleep()


    def calibrate_left_grip(self):
        grip_left = baxter_interface.Gripper('left', CHECK_VERSION)
        grip_left.calibrate()

    def calibrate_right_grip(self):
        grip_right = baxter_interface.Gripper('right', CHECK_VERSION)
        grip_right.calibrate()

    def calibrate_grips(self):
        grip_right = baxter_interface.Gripper('right', CHECK_VERSION)
        grip_left = baxter_interface.Gripper('left', CHECK_VERSION)
        sleep_count = 0
        grip_right.calibrate()
        grip_left.calibrate()

#        while not rospy.is_shutdown():
#            rospy.sleep(0.01)
#            print(sleep_count)
#            sleep_count += 1
#            if sleep_count > 10:
#                break

    def open_grip(self, limb):
        grip_right = baxter_interface.Gripper(limb, CHECK_VERSION)
        sleep_count = 0
        while not rospy.is_shutdown():
            grip_right.open()
            rospy.sleep(0.01)
            sleep_count += 1
            if sleep_count > 100:
                break

    def open_grips(self):
        grip_right = baxter_interface.Gripper('right', CHECK_VERSION)
        grip_left = baxter_interface.Gripper('left', CHECK_VERSION)
        sleep_count = 0
        while not rospy.is_shutdown():
            grip_right.open()
            grip_left.open()
            rospy.sleep(0.01)
            sleep_count += 1
            if sleep_count > 10:
                break

    def close_grips(self):
        grip_right = baxter_interface.Gripper('right', CHECK_VERSION)
        grip_left = baxter_interface.Gripper('left', CHECK_VERSION)
        sleep_count = 0
        while not rospy.is_shutdown():
            grip_right.close()
            grip_left.close()
            rospy.sleep(0.01)
            sleep_count += 1
            if sleep_count > 10:
                break

    def fold_left_sleeve(self):
        self.open_grips()
        left_grip, right_grip = [0.91, 0.37, 0.04], [0.37, 0.24, 0.034]

        left_grip_up = left_grip[:]
        left_grip_up[1] -= 0.1
        left_grip_up[2] = 0.15

        right_grip_up = right_grip[:]
        right_grip_up[1] -= 0.1
        right_grip_up[2] = 0.1

        left_grip_in = left_grip_up[:]
        left_grip_in[1] -= 0.35
        left_grip_in[2] = 0.05

        right_grip_in = right_grip_up[:]
        right_grip_in[1] -= 0.3 / 2.4
        right_grip_in[2] = 0.05

        left_grip_in_up = left_grip_in[:]
        left_grip_in_up[1] += 0.1
        left_grip_in_up[2] += 0.05

        right_grip_in_up = right_grip_in[:]
        right_grip_in_up[2] += 0.05

        left_grip_exit = left_grip_in[:]
        left_grip_exit[1] -= 0.1

        right_grip_exit = right_grip_in[:]
        right_grip_exit[1] -= 0.1

        print('left_grip_up', left_grip_up)
        print('right_grip_up', right_grip_up)
        self.move_arms(left_grip_up, right_grip_up)
        rospy.sleep(1.5)
        self.move_arms(left_grip, right_grip)
        rospy.sleep(1.5)
        self.close_grips()
        rospy.sleep(1.5)
        # self.move_arms(left_grip_up, right_grip_up)
        # rospy.sleep(1.5)
        self.move_arms(left_grip_in_up, right_grip_in_up)
        # rospy.sleep(1.5)
        self.move_arms(left_grip_in, right_grip_in)
        rospy.sleep(1.5)
        self.open_grips()
        rospy.sleep(1.5)
        self.move_arms(left_grip_exit, right_grip_exit)

    def fold_right_sleeve(self):
        self.open_grips()
        right_grip, left_grip = [0.91, -0.37, 0.04], [0.37, -0.24, 0.034]

        left_grip_up = left_grip[:]
        left_grip_up[1] += 0.1
        left_grip_up[2] = 0.1

        right_grip_up = right_grip[:]
        right_grip_up[1] += 0.1
        right_grip_up[2] = 0.1

        left_grip_in = left_grip_up[:]
        left_grip_in[1] += 0.3 / 2.4
        left_grip_in[2] = 0.05

        right_grip_in = right_grip_up[:]
        right_grip_in[1] += 0.35
        right_grip_in[2] = 0.05

        left_grip_in_up = left_grip_in[:]
        left_grip_in_up[2] += 0.05

        right_grip_in_up = right_grip_in[:]
        right_grip_in_up[1] -= 0.1
        right_grip_in_up[2] += 0.05

        left_grip_exit = left_grip_in[:]
        left_grip_exit[1] += 0.1

        right_grip_exit = right_grip_in[:]
        right_grip_exit[1] += 0.1

        self.move_arms(left_grip_up, right_grip_up)
        rospy.sleep(1.5)
        self.move_arms(left_grip, right_grip)
        rospy.sleep(1.5)
        self.close_grips()
        rospy.sleep(1.5)
        # self.move_arms(left_grip_up, right_grip_up)
        # rospy.sleep(1.5)
        self.move_arms(left_grip_in_up, right_grip_in_up)
        # rospy.sleep(1.5)
        self.move_arms(left_grip_in, right_grip_in)
        rospy.sleep(1.5)
        self.open_grips()
        rospy.sleep(1.5)
        self.move_arms(left_grip_exit, right_grip_exit)

    def fold_bottom_to_top(self):
        y_offset = 0.1
        left_grip, right_grip = [0.35, y_offset, 0.028], [0.35, -y_offset, 0.028]
        left_grip_up, right_grip_up = [0.4, y_offset, 0.1], [0.4, -y_offset, 0.1]
        left_grip_forward, right_grip_forward = [0.65, y_offset, 0.1], [0.65, -y_offset, 0.1]
        left_grip_forward_down, right_grip_forward_down = [0.7, y_offset, 0.1], [0.7, -y_offset, 0.1]

        self.open_grips()
        self.move_arms(left_grip, right_grip)
        rospy.sleep(0.5)
        self.close_grips()
        rospy.sleep(0.5)
        self.move_arms(left_grip_up, right_grip_up)
        rospy.sleep(0.5)
        self.move_arms(left_grip_forward, right_grip_forward)
        rospy.sleep(0.5)
        self.move_arms(left_grip_forward_down, right_grip_forward_down)
        self.open_grips()
        self.move_arms(left_grip_forward, right_grip_forward)

    def fold_top_to_bottom(self):
        y_offset = 0.1
        left_grip, right_grip = [0.98, y_offset, 0.02], [0.98, -y_offset, 0.02]
        left_grip_up, right_grip_up = [0.9, y_offset, 0.2], [0.9, -y_offset, 0.2]
        left_grip_backward, right_grip_backward = [0.6, y_offset, 0.1], [0.6, -y_offset, 0.1]
        left_grip_backward_down, right_grip_backward_down = [0.6, y_offset, 0.1], [0.6, -y_offset, 0.1]

        self.open_grips()
        self.move_arms(left_grip_up, right_grip_up)
        rospy.sleep(0.5)
        self.move_arms(left_grip, right_grip)
        rospy.sleep(0.5)
        self.close_grips()
        rospy.sleep(0.5)
        self.move_arms(left_grip_up, right_grip_up)
        rospy.sleep(0.5)
        self.move_arms(left_grip_backward, right_grip_backward)
        rospy.sleep(0.5)
        self.move_arms(left_grip_backward_down, right_grip_backward_down)
        self.open_grips()

    def scoop_from_left(self, target):
        scoop_quat = Quaternion(x=-0.27526253471, y=0.841704081393, z=-0.428598464611, w=0.179075773025)
        left_of_target = [target[0], target[1] + 0.1, target[2] - 0.005]
        above_left_of_target = [left_of_target[0], left_of_target[1] - 0.1, left_of_target[2] + 0.1]
        print('Moving left of target')
        self.move_arm('left', left_of_target[0], left_of_target[1], left_of_target[2], scoop_quat)
        rospy.sleep(1)
        print('Moving to target')
        self.move_arm('left', target[0], target[1], target[2] - 0.005, scoop_quat)
        rospy.sleep(1)
        self.close_grip('left')
        self.move_arm('left', above_left_of_target[0], above_left_of_target[1], above_left_of_target[2], scoop_quat)
        self.open_grip('left')

    def scoop(self, limb, target):
        if limb == 'left':
            self.scoop_from_left(target)

# grip [0.493987,0.209845,0.16986],
# release [0.968917,-0.148964,0.164326],
# grip2 [0.517411,-0.137215,0.164335],
# release2 [0.185002,-0.0114352,0.893222]
def callback(data):
    print(data)
    folder = ClothFolder()
    data_arr = data.data.split(',')
    num_hands = float(data_arr[0])
    grip_goal_x = float(data_arr[1])
    grip_goal_y = float(data_arr[2])
    grip_goal_z = float(data_arr[3])
    release_goal_x = float(data_arr[4])
    release_goal_y = float(data_arr[5])
    release_goal_z = float(data_arr[6])
    grip = [grip_goal_x, grip_goal_y, grip_goal_z]
    release = [release_goal_x, release_goal_y, release_goal_z]

    right_grip_ik = folder.compute_ik('right', grip, grip_quat_near_right)
    left_grip_ik = folder.compute_ik('left', grip, grip_quat_near_left)

    pos_right = folder._right_arm.endpoint_pose()['position']
    pos_left = folder._left_arm.endpoint_pose()['position']
    pos_right_arr = [pos_right.x, pos_right.y, pos_right.z]
    pos_left_arr = [pos_left.x, pos_left.y, pos_left.z]





def main():
    rospy.init_node('baxter_cloth_folder')
    rospy.Subscriber("/vcla/cloth_folding/action", String, callback)

    folder = ClothFolder(single_arm_mode='left')

    target_point = [0.798136304356 + 0.1, 0.188754932759, 0.0834009411523]
    folder.scoop('left', target_point)

    target_point = [0.798136304356 + 0.05, 0.188754932759, 0.0834009411523]
    folder.scoop('left', target_point)

    target_point = [0.798136304356, 0.188754932759, 0.0834009411523]
    folder.scoop('left', target_point)

    target_point = [0.798136304356 - 0.05, 0.188754932759, 0.0834009411523]
    folder.scoop('left', target_point)

    target_point = [0.798136304356 - 0.1, 0.188754932759, 0.0834009411523]
    folder.scoop('left', target_point)

    rospy.spin()


if __name__ == '__main__':
    main()
