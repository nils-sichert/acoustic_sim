#!/usr/bin/env python

from datetime import time
from tqdm import tqdm
import numpy as np
import rospy
import threading
from acoustic_sim.acoustic_sim_class import acousticSimulation
from acoustic_sim.localisation_sim_class import localisationSimulation
from acoustic_sim.dataloader_class import dataLoader
from nav_msgs.msg import Odometry
from geometry_msgs.msg import PointStamped
from std_msgs.msg import Float32
from sensor_msgs.msg import FluidPressure
from acoustic_sim.msg import ModemOut
import json
import os

class simulation():
    def __init__(self):

        tmp = os.path.dirname(__file__)
        file_path_filter = os.path.join(tmp, '../config/acoustic_config.json')
        f = open(file_path_filter)
        self.acoustic_config = json.load(f)
        f.close()

        file_path_filter = os.path.join(tmp, '../config/filter_config.json')
        f = open(file_path_filter)
        self.filter_config = json.load(f)
        f.close()

        self.t0 = 0
        self.dt = 0
        self.t = self.t0
        self.last_t = self.t0
        self.z = None
        self.x0 = self.filter_config["config"][1]["settings"]["InitState"]
        self.x = None
        self.x_est = None
        self.statePre = None
        self.covarPre = None
        self.p_mat = None
        self.ros = self.filter_config["config"][0]["RosRun"]

        self.lock = threading.RLock()
        self.position = [0, 0, 0]
        self.velocity = [0, 0, 0]
        self.depth = 0

        # settings from Configfile
        self.f_pre = self.filter_config["config"][0]["PredictFrequency"]
        self.f_ac = self.acoustic_config["config"][0]["FrequencyAcousticSim"]
        
        self.acoustic_sim = acousticSimulation()
        self.localisation_sim = localisationSimulation()

        if self.ros:
            rospy.init_node("simulation")
            self.position_pub = rospy.Publisher("predict_state", PointStamped, queue_size=1)
            self.position_upd_pub0 = rospy.Publisher("update_state0", PointStamped, queue_size=1)
            self.position_upd_pub1 = rospy.Publisher("update_state1", PointStamped, queue_size=1)
            self.position_upd_pub2 = rospy.Publisher("update_state2", PointStamped, queue_size=1)
            self.position_upd_pub3 = rospy.Publisher("update_state3", PointStamped, queue_size=1)
            self.errAbs = rospy.Publisher("errAbs", PointStamped, queue_size=1)
            self.ModemOut0 = rospy.Publisher("ModemOut0", ModemOut, queue_size= 1)
            self.ModemOut1 = rospy.Publisher("ModemOut1", ModemOut, queue_size= 1)
            self.ModemOut2 = rospy.Publisher("ModemOut2", ModemOut, queue_size= 1)
            self.ModemOut3 = rospy.Publisher("ModemOut3", ModemOut, queue_size= 1)
            self.acousticError0 = rospy.Publisher("AcouERR0", ModemOut, queue_size=1)
            self.acousticError1 = rospy.Publisher("AcouERR1", ModemOut, queue_size=1)
            self.acousticError2 = rospy.Publisher("AcouERR2", ModemOut, queue_size=1)
            self.acousticError3 = rospy.Publisher("AcouERR3", ModemOut, queue_size=1)
            self.Anchor0 = rospy.Publisher("PosAnchor0", PointStamped, queue_size=1)
            self.Anchor1 = rospy.Publisher("PosAnchor1", PointStamped, queue_size=1)
            self.Anchor2 = rospy.Publisher("PosAnchor2", PointStamped, queue_size=1)
            self.Anchor3 = rospy.Publisher("PosAnchor3", PointStamped, queue_size=1)
            if self.acoustic_config["config"][0]["SimulationPath"]:
                self.position_sub = rospy.Subscriber("/bluero/ground_truth/state", Odometry, self.subscrib_position)
            else:
                self.position_sub = rospy.Subscriber("ground_truth/state", Odometry, self.subscrib_position)
            self.depth_sub = rospy.Subscriber("depth", Float32, self.subscrib_depth)
    
    def subscrib_position(self, msg: Odometry):
        pos = msg.pose.pose.position
        v = msg.twist.twist.linear
        with self.lock:
            self.position = [pos.x, pos.y, pos.z]
            self.velocity = [v.x, v.y, v.z]
    
    def subscrib_depth(self, msg: Float32):
        with self.lock:
            self.depth = msg.data
 

    def publish_position(self, position, t, publisher: rospy.Publisher):
        msg = PointStamped()
        msg.header.stamp = rospy.Time.from_sec(t)
        msg.header.frame_id = "map"
        msg.point.x = position[0]
        msg.point.y = position[1]
        msg.point.z = position[2]
        publisher.publish(msg)

    def publish_acousticMeas(self,index, meas, t, publisher: rospy.Publisher):
        msg = ModemOut()
        msg.dist = meas
        msg.id = index
        msg.timestamp = rospy.Time.from_sec(t)
        publisher.publish(msg)
    
    def publish_ErrorBeacon(self, BeaconIndex, meas, t, x, publisher: rospy.Publisher):
        BeaconPos = self.getBeaconPos(BeaconIndex)
        x = np.array(x)
        zhat = np.linalg.norm(BeaconPos-x)
        dz = zhat - meas
        msg = ModemOut()
        msg.dist = dz
        msg.id = BeaconIndex
        msg.timestamp = rospy.Time.from_sec(t)
        publisher.publish(msg)
    
    def publish_ErrorAbs(self, x, t, x_est, publisher: rospy.Publisher):
        dx = x - x_est
        msg = PointStamped()
        msg.header.stamp = rospy.Time.from_sec(t)
        msg.header.frame_id = "map"
        msg.point.x = dx[0]
        msg.point.y = dx[1]
        msg.point.z = dx[2]
        publisher.publish(msg)


    def getBeaconPos(self, BeaconIndex):
        for i in self.acoustic_config["config"]:
            if i["type"] == "anchor":
                if i["modem"]["id"] == BeaconIndex:
                    return i["position"]

    def run(self):
        if self.ros:
            r = rospy.Rate(self.acoustic_config["config"][0]["FrequencyAcousticSim"])
            counter = 1
            steps = round(self.f_ac/ self.f_pre,0)
            while not rospy.is_shutdown():
                with self.lock:
                    x = self.position
                    preInput = self.velocity + np.random.normal(self.filter_config["config"][0]["MeasErrLoc"],self.filter_config["config"][0]["MeasErrScale"],3)
                    depth = self.depth
                    t = rospy.get_time()
                    meas = self.acoustic_sim.simulate(x, t)
                    
                    if meas is not None:
                        xupd = self.localisation_sim.locate(preInput, t, depth, meas)
                        #print("Xupd: ",xupd)
                        #self.publish_position(xupd, t, self.position_pub)
                        # meas: 0-Index, 1-dist, 2-time
                        # {"dist": dist, "time_published": exittime, "ModemID": ID}
                        if meas["ModemID"] == 1:
                            self.publish_acousticMeas(meas["ModemID"],meas["dist"],meas["time_published"], self.ModemOut0)
                            self.publish_position(xupd, t, self.position_upd_pub0)
                            self.publish_ErrorBeacon(meas["ModemID"], meas["dist"], t, x, self.acousticError0)
                            self.publish_position(meas["ModemPos"], t, self.Anchor0)
                        elif meas["ModemID"] == 2:
                            self.publish_acousticMeas(meas["ModemID"],meas["dist"],meas["time_published"], self.ModemOut1)
                            self.publish_position(xupd, t, self.position_upd_pub1)
                            self.publish_ErrorBeacon(meas["ModemID"], meas["dist"], t, x, self.acousticError1)
                            self.publish_position(meas["ModemPos"], t, self.Anchor1)
                        elif meas["ModemID"] == 3:
                            self.publish_acousticMeas(meas["ModemID"],meas["dist"],meas["time_published"], self.ModemOut2)
                            self.publish_position(xupd, t, self.position_upd_pub2)
                            self.publish_ErrorBeacon(meas["ModemID"], meas["dist"], t, x, self.acousticError2)
                            self.publish_position(meas["ModemPos"], t, self.Anchor2)
                        elif meas["ModemID"] == 4:
                            self.publish_acousticMeas(meas["ModemID"],meas["dist"],meas["time_published"], self.ModemOut3)
                            self.publish_position(xupd, t, self.position_upd_pub3)
                            self.publish_ErrorBeacon(meas["ModemID"], meas["dist"], t, x, self.acousticError3)
                            self.publish_position(meas["ModemPos"], t, self.Anchor3)


                    elif counter == steps:
                        xest = self.localisation_sim.locate(preInput, t, depth, meas=None)
                        self.publish_position(xest, t, self.position_pub)
                        self.publish_ErrorAbs(x, t, xest, self.errAbs)
                      
                        counter = 1
                    else:
                        counter +=1
                r.sleep()

        elif not self.ros:
            v = dataLoader.v
            preInput = dataLoader.vn
            t = dataLoader.t
            steps = len(t)
            counter = 1
            steps = round(self.f_ac/ self.f_pre,0)
            length = len(t)
            for i in tqdm(range(length), ncols=100):
                x = self.x0 + np.array(v[i]) * self.dt
                self.x0 = x
                meas = self.acoustic_sim.simulate(x, t[i])
                if meas is not None:
                    self.localisation_sim.locate(preInput[i], t[i], x[2], meas)
                    
                elif counter == steps:
                    self.localisation_sim.locate(preInput[i], t[i], x[2], meas=None)
                    counter = 1
                else:
                    counter +=1   

def main():
    simu = simulation()
    simu.run()

if __name__ == "__main__":
    main()


