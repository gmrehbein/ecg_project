import numpy as np
from scipy.signal import butter, find_peaks, iirnotch, sosfilt, sosfilt_zi, tf2sos


class FilterEngine:
    """
    Real-time ECG filter for 3-channel signals (RA, LA, LL) that removes power-line
    interference, DC offset, and high-frequency EMG noise.

    This class applies:
    - A second-order Butterworth bandpass filter (default 0.5–40 Hz)
    - A notch filter (default 60 Hz) to suppress power-line interference

    Filters are applied using second-order sections (SOS) for numerical stability,
    and state vectors are maintained per-channel for real-time streaming support.

    Parameters:
        fs (float): Sampling frequency in Hz. Default is 100.
        bandpass (tuple): Bandpass filter cutoff frequencies (low, high) in Hz.
                          Default is (0.5, 40).
        notch_freq (float): Center frequency for the notch filter (e.g., 50 or 60 Hz).
                            Default is 60.0 (USA/CAN power line frequency).
        notch_Q (float): Quality factor for the notch filter. Default is 30.0.

    Methods:
        filter(signal: np.ndarray) -> np.ndarray:
            Filters a single ECG sample of shape (3,) and returns the filtered result.
            The input should represent the electrode potentials [RA, LA, LL].
    """

    def __init__(
        self, fs: float = 100, bandpass=(0.5, 40), notch_freq=60.0, notch_Q=30.0
    ):
        nyq = 0.5 * fs

        # --- Bandpass SOS ---
        self.sos_band = butter(
            N=2,
            Wn=[bandpass[0] / nyq, bandpass[1] / nyq],
            btype="bandpass",
            output="sos",
        )
        self.zi_band = [sosfilt_zi(self.sos_band) for _ in range(3)]  # 3 channels

        # --- Notch SOS ---
        b_notch, a_notch = iirnotch(w0=notch_freq / fs, Q=notch_Q)
        self.sos_notch = tf2sos(b_notch, a_notch)
        self.zi_notch = [sosfilt_zi(self.sos_notch) for _ in range(3)]

    def filter(self, signal: np.ndarray) -> np.ndarray:

        x = signal.reshape(1, 3)  # shape (1, 3)

        y_band = np.zeros_like(x)
        for ch in range(3):
            y_band[:, ch], self.zi_band[ch] = sosfilt(
                self.sos_band, x[:, ch], zi=self.zi_band[ch]
            )

        y_notch = np.zeros_like(y_band)
        for ch in range(3):
            y_notch[:, ch], self.zi_notch[ch] = sosfilt(
                self.sos_notch, y_band[:, ch], zi=self.zi_notch[ch]
            )

        return y_notch[0]  # shape (3,)


class SingleChannelHRMonitor:
    """
    Real-time heart rate monitor for a single ECG lead (typically Lead II or aVF).

    This class maintains a circular buffer of incoming ECG samples and timestamps,
    performs real-time R-peak detection over a sliding window, and estimates heart
    rate (BPM) based on recent RR intervals.

    Parameters:
        fs (int): Sampling frequency in Hz. Default is 100 Hz.
        buffer_duration_sec (int): Duration of signal to retain in the circular buffer (in seconds).
                                   Default is 10 seconds.
        rr_buffer_size (int): Number of recent RR intervals to average when computing BPM.
                              Default is 8.

    Attributes:
        values (np.ndarray): Circular buffer storing the most recent ECG samples.
        timestamps (np.ndarray): Circular buffer storing the corresponding timestamps.
        r_peak_times (list): Detected R-peak timestamps.
        rr_intervals (list): Recent RR intervals used to compute BPM.

    Methods:
        append(value, timestamp):
            Append a new sample to the circular buffer.

        size():
            Return the number of valid samples currently stored.

        is_ready(window_size=100):
            Return True if the buffer has enough data for analysis.

        process_latest_window(window_size=100):
            Detect R-peaks in the latest window and update RR intervals.
            Returns the current BPM estimate if available, else None.

        compute_bpm():
            Compute the current heart rate based on recent RR intervals.
            Returns BPM as float, or None if not enough RR intervals are present.
    """

    def __init__(self, fs=100, buffer_duration_sec=10, rr_buffer_size=8):
        self.fs = fs
        self.capacity = int(fs * buffer_duration_sec)

        self.values = np.zeros(self.capacity, dtype=np.float32)
        self.timestamps = np.zeros(self.capacity, dtype=np.float64)

        self._start = 0
        self._count = 0

        self.min_distance = int(0.3 * fs)  # Min RR: 200 bpm
        self.r_peak_times = []
        self.rr_intervals = []
        self.rr_buffer_size = rr_buffer_size

    def append(self, value: float, timestamp: float):
        idx = (self._start + self._count) % self.capacity
        self.values[idx] = value
        self.timestamps[idx] = timestamp

        if self._count < self.capacity:
            self._count += 1
        else:
            self._start = (self._start + 1) % self.capacity

    def size(self):
        return self._count

    def is_ready(self, window_size=100):
        return self._count >= window_size

    def process_latest_window(self, window_size=100):
        """
        Detect R-peaks and compute BPM from the last N samples.
        """
        if not self.is_ready(window_size):
            return None

        indices = (
            self._start + self._count - window_size + np.arange(window_size)
        ) % self.capacity
        signal = self.values[indices]
        timestamps = self.timestamps[indices]

        # R-peak detection
        peaks, _ = find_peaks(
            signal,
            height=np.max(signal) * 0.5,
            distance=self.min_distance,
            prominence=0.3,
        )
        peak_times = timestamps[peaks]

        for t in peak_times:
            if not self.r_peak_times or (t - self.r_peak_times[-1]) > (
                self.min_distance / self.fs
            ):
                self._update_rr_intervals(t)
                self.r_peak_times.append(t)

        return self.compute_bpm()

    def _update_rr_intervals(self, new_time):
        if self.r_peak_times:
            rr = new_time - self.r_peak_times[-1]
            if 0.25 < rr < 2.0:  # Reasonable RR interval
                self.rr_intervals.append(rr)
                if len(self.rr_intervals) > self.rr_buffer_size:
                    self.rr_intervals.pop(0)

    def compute_bpm(self):
        if len(self.rr_intervals) < 2:
            return None
        return 60.0 / np.mean(self.rr_intervals)


def compute_leads_from_sample(signal: np.ndarray) -> dict:
    """
    Compute the six derived ECG leads (I, II, III, aVR, aVL, aVF) from a single
    filtered 3-electrode sample (RA, LA, LL) using the standard Einthoven and Goldberger formulas

    Parameters:
        signal (np.ndarray): A 1D NumPy array of shape (3,) representing [RA, LA, LL]

    Returns:
        dict: A dictionary mapping lead names to their computed float values
    """
    RA, LA, LL = signal
    return {
        "I": LA - RA,
        "II": LL - RA,
        "III": LL - LA,
        "aVR": RA - (LA + LL) / 2,
        "aVL": LA - (RA + LL) / 2,
        "aVF": LL - (RA + LA) / 2,
    }
