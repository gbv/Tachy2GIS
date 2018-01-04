
# -*- coding=utf-8 -*-

# Bearbeiter ----------------------------------------------------------------------------------------------------------#

# MODULE-------------------------------------------------------------------------------------------------------------- #

import serial
from PyQt4.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer
from qgis.core import QgsMessageLog


class TachyReader(QObject):
    lineReceived = pyqtSignal(str)
    pollingInterval = 1000
    def __init__(self, port, baudRate, parent=None):
        super(self.__class__, self).__init__(parent)
        self.pollingTimer = QTimer()
        self.pollingTimer.timeout.connect(self.poll)
        # A measurement takes roughly two seconds. timeout is provided in seconds (polling interval in milliseconds).
        try:
            self.ser = serial.Serial(port, baudRate, timeout=0.2)
        except serial.SerialException:
            pass

    def poll(self):
        if self.ser.inWaiting():
            line = self.ser.readline()
            self.lineReceived.emit(line)
            #QgsMessageLog.logMessage(line, 'Serial', QgsMessageLog.INFO)

    def beginListening(self):
        self.pollingTimer.start(self.pollingInterval)

    @pyqtSlot()
    def shutDown(self):
        if self.ser.isOpen():
            self.ser.close()
        self.pollingTimer.stop()