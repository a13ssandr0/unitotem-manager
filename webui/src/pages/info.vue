<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import Chart from 'chart.js/auto';

const info = ref<any>(null);
const cpuChartCanvas = ref<HTMLCanvasElement | null>(null);
let cpuChart: Chart | null = null;

const chartColors = {
    red: 'rgb(255, 99, 132)',
    orange: 'rgb(255, 159, 64)',
    yellow: 'rgb(255, 205, 86)',
    green: 'rgb(75, 192, 192)',
    blue: 'rgb(54, 162, 235)',
    purple: 'rgb(153, 102, 255)',
    grey: 'rgb(201, 203, 207)'
};

const chartColors_transparent = {
    red: 'rgba(255, 99, 132, 0.5)',
    orange: 'rgba(255, 159, 64, 0.5)',
    yellow: 'rgba(255, 205, 86, 0.5)',
    green: 'rgba(75, 192, 192, 0.5)',
    blue: 'rgba(54, 162, 235, 0.5)',
    purple: 'rgba(153, 102, 255, 0.5)',
    grey: 'rgba(201, 203, 207, 0.5)'
};

const fetchData = async () => {
  // Simulate fetching data
  const jsonData = {"target": "Settings/info", "uptime": "2 days, 21:27:41", "cpu": [{"user": 12.5, "nice": 0.0, "system": 6.2, "idle": 81.1, "iowait": 0.2, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 18.7}, {"user": 11.6, "nice": 0.0, "system": 6.8, "idle": 81.6, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 18.4}, {"user": 15.9, "nice": 0.0, "system": 45.7, "idle": 38.4, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 61.6}, {"user": 8.0, "nice": 0.0, "system": 3.0, "idle": 88.7, "iowait": 0.3, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 11.0}, {"user": 8.4, "nice": 0.0, "system": 1.7, "idle": 90.0, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 10.1}, {"user": 18.9, "nice": 0.0, "system": 3.7, "idle": 77.1, "iowait": 0.3, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 22.6}, {"user": 6.5, "nice": 0.0, "system": 2.0, "idle": 91.5, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 8.5}, {"user": 6.0, "nice": 0.0, "system": 2.0, "idle": 92.0, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 8.0}, {"user": 5.0, "nice": 0.0, "system": 1.7, "idle": 92.7, "iowait": 0.7, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 6.7}, {"user": 5.0, "nice": 0.0, "system": 1.3, "idle": 93.7, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 6.3}, {"user": 14.7, "nice": 0.0, "system": 5.7, "idle": 79.7, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 20.4}, {"user": 52.6, "nice": 0.0, "system": 14.2, "idle": 33.1, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 66.8}, {"user": 23.1, "nice": 0.0, "system": 12.9, "idle": 64.0, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 36.0}, {"user": 8.4, "nice": 0.0, "system": 1.0, "idle": 90.6, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 9.4}, {"user": 5.0, "nice": 0.0, "system": 1.0, "idle": 93.6, "iowait": 0.3, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 6.0}, {"user": 13.7, "nice": 0.0, "system": 4.0, "idle": 82.3, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 17.7}, {"user": 4.0, "nice": 0.0, "system": 3.0, "idle": 91.7, "iowait": 0.7, "irq": 0.0, "softirq": 0.7, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 7.7}, {"user": 7.2, "nice": 0.0, "system": 1.3, "idle": 90.5, "iowait": 1.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 8.5}, {"user": 9.0, "nice": 0.0, "system": 2.0, "idle": 89.0, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 11.0}, {"user": 5.0, "nice": 0.0, "system": 2.0, "idle": 93.0, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 7.0}, {"user": 20.9, "nice": 0.0, "system": 7.6, "idle": 71.4, "iowait": 0.0, "irq": 0.0, "softirq": 0.0, "steal": 0.0, "guest": 0.0, "guest_nice": 0.0, "total": 28.5}], "battery": null, "fans": [["fan-nct6798-0", 0], ["fan-nct6798-1", 4687], ["fan-nct6798-2", 0], ["fan-nct6798-3", 0], ["fan-nct6798-4", 0], ["fan-nct6798-5", 0]], "temperatures": [["temp-nvme-b300-Composite", 38.85], ["temp-nvme-b300-Sensor1", 38.85], ["temp-nvme-b300-Sensor2", 37.85], ["temp-nvme-b400-Composite", 37.85], ["temp-nvme-b400-Sensor1", 37.85], ["temp-nvme-b400-Sensor2", 39.85], ["temp-coretemp-Packageid0", 53.0], ["temp-coretemp-Core10", 42.0], ["temp-coretemp-Core11", 42.0], ["temp-coretemp-Core12", 42.0], ["temp-coretemp-Core13", 53.0], ["temp-coretemp-Core0", 50.0], ["temp-coretemp-Core1", 53.0], ["temp-coretemp-Core2", 44.0], ["temp-coretemp-Core3", 43.0], ["temp-coretemp-Core5", 43.0], ["temp-coretemp-Core6", 40.0], ["temp-nct6798-SYSTIN", 29.0], ["temp-nct6798-PCH_CHIP_CPU_MAX_TEMP", 0.0], ["temp-nct6798-PCH_CHIP_TEMP", 0.0], ["temp-nct6798-PCH_CPU_TEMP", 0.0], ["temp-nct6798-CPUTIN", 41.5], ["temp-nct6798-AUXTIN0", 50.0], ["temp-nct6798-AUXTIN1", 16.0], ["temp-nct6798-AUXTIN2", 25.0], ["temp-nct6798-AUXTIN3", 63.0], ["temp-nct6798-AUXTIN4", 94.0], ["temp-nct6798-PECIAgent0", 54.0], ["temp-nct6798-PECIAgent0Calibration", 44.5], ["temp-iwlwifi_1-0", 43.0]], "vmem": {"total": 33310904320, "available": 14832533504, "percent": 55.5, "used": 18478370816, "free": 5105627136, "active": 21458100224, "inactive": 3811082240, "buffers": 790642688, "cached": 10136772608, "shared": 325963776, "slab": 1823576064}};
  info.value = jsonData;
  updateChartData();
};

const initializeChart = () => {
  if (!cpuChartCanvas.value) return;

  const ctx = cpuChartCanvas.value.getContext('2d');
  if (ctx) {
    cpuChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: [],
        datasets: getDatasets([])
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'top',
          },
        },
        scales: {
          x: {
            stacked: true,
          },
          y: {
            stacked: true,
            min: 0,
            max: 100
          }
        }
      }
    });
  }
};

const updateChartData = () => {
  if (!info.value || !cpuChart) return;

  const cpuData = info.value.cpu;
  cpuChart.data.labels = cpuData.map((_, index) => `Core ${index}`);
  const newDatasets = getDatasets(cpuData);

  cpuChart.data.datasets.forEach((dataset, index) => {
    dataset.data = newDatasets[index].data;
  });

  cpuChart.update();
};

const getDatasets = (cpuData: any[]) => {
  return [
    {
      label: 'Low (Nice)',
      backgroundColor: chartColors_transparent.blue,
      borderColor: chartColors.blue,
      borderWidth: 1,
      stack: 'stack0',
      data: cpuData.map(cpu => cpu.nice)
    },
    {
      label: 'Normal (User)',
      backgroundColor: chartColors_transparent.green,
      borderColor: chartColors.green,
      borderWidth: 1,
      stack: 'stack0',
      data: cpuData.map(cpu => cpu.user)
    },
    {
      label: 'Kernel',
      backgroundColor: chartColors_transparent.red,
      borderColor: chartColors.red,
      borderWidth: 1,
      stack: 'stack0',
      data: cpuData.map(cpu => cpu.system)
    },
    {
      label: 'IRQ',
      backgroundColor: chartColors_transparent.yellow,
      borderColor: chartColors.yellow,
      borderWidth: 1,
      stack: 'stack0',
      data: cpuData.map(cpu => cpu.irq)
    },
    {
      label: 'Soft IRQ',
      backgroundColor: chartColors_transparent.purple,
      borderColor: chartColors.purple,
      borderWidth: 1,
      stack: 'stack0',
      data: cpuData.map(cpu => cpu.softirq)
    },
    {
      label: 'IO Wait',
      backgroundColor: chartColors_transparent.grey,
      borderColor: chartColors.grey,
      borderWidth: 1,
      stack: 'stack0',
      data: cpuData.map(cpu => cpu.iowait)
    }
  ];
};

const formatBytes = (bytes: number) => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const ramAppUsed = computed(() => {
  if (!info.value) return 0;
  const vmem = info.value.vmem;
  // App Used = Total - Free - Buffers - Cached
  return vmem.total - vmem.free - vmem.buffers - vmem.cached;
});

const ramUsedPerc = computed(() => {
  if (!info.value) return 0;
  return (ramAppUsed.value / info.value.vmem.total) * 100;
});

const ramBuffersPerc = computed(() => {
  if (!info.value) return 0;
  return (info.value.vmem.buffers / info.value.vmem.total) * 100;
});

const ramCachedPerc = computed(() => {
  if (!info.value) return 0;
  return (info.value.vmem.cached / info.value.vmem.total) * 100;
});


onMounted(() => {
  initializeChart();
  fetchData();
  setInterval(fetchData, 5000); // Refresh data every 5 seconds
});
</script>

<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12" v-if="info">
        <p class="text-h6">Uptime: {{ info.uptime }}</p>
      </v-col>
    </v-row>
    <v-row>
      <v-col cols="12" md="6" lg="4">
        <v-card>
          <v-card-title>CPU</v-card-title>
          <v-card-text>
            <canvas ref="cpuChartCanvas" style="min-height: 400px;"></canvas>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="6" lg="4" v-if="info">
        <v-card>
          <v-card-title>Memory</v-card-title>
          <v-card-text>
            <v-sheet width="100%" height="20px" color="surface-variant" class="d-flex my-4 rounded-xl overflow-hidden">
              <v-sheet color="success" height="100%" :width="`${ramUsedPerc}%`" />
              <v-sheet color="primary" height="100%" :width="`${ramBuffersPerc}%`" />
              <v-sheet color="warning" height="100%" :width="`${ramCachedPerc}%`" />
            </v-sheet>
            <div class="d-flex flex-wrap justify-space-between ga-2">
              <v-chip color="success" size="small">Used: {{ formatBytes(ramAppUsed) }}</v-chip>
              <v-chip color="primary" size="small">Buffers: {{ formatBytes(info.vmem.buffers) }}</v-chip>
              <v-chip color="warning" size="small">Cached: {{ formatBytes(info.vmem.cached) }}</v-chip>
              <v-chip color="surface-variant" size="small">Free: {{ formatBytes(info.vmem.free) }}</v-chip>
              <v-chip size="small">Total: {{ formatBytes(info.vmem.total) }}</v-chip>
            </div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="6" lg="4" v-if="info">
        <v-card>
          <v-card-title>Temperatures</v-card-title>
          <v-card-text>
            <v-list dense>
              <v-list-item v-for="(temp, index) in info.temperatures" :key="index">
                <v-list-item-content>
                  <v-list-item-title>{{ temp[0] }}: {{ temp[1] }} °C</v-list-item-title>
                </v-list-item-content>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="6" lg="4" v-if="info">
        <v-card>
          <v-card-title>Fans</v-card-title>
          <v-card-text>
            <v-list dense>
              <v-list-item v-for="(fan, index) in info.fans" :key="index">
                <v-list-item-content>
                  <v-list-item-title>{{ fan[0] }}: {{ fan[1] }} RPM</v-list-item-title>
                </v-list-item-content>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<style scoped>
.text-h6 {
  margin-bottom: 1rem;
}
</style>
