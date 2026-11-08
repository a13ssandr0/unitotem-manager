<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12" v-if="info">
        <h2 class="text-h5">{{ info.machine || 'This device' }}</h2>
        <p class="text-subtitle-1 text-medium-emphasis">Uptime: {{ info.uptime }}</p>
      </v-col>
    </v-row>
    <v-row>
      <v-col cols="12" md="6" lg="4">
        <v-card class="mb-6">
          <v-card-title>CPU<span v-if="info?.cpu_name" class="text-medium-emphasis"> ({{ info.cpu_name }})</span></v-card-title>
          <v-card-text>
            <canvas ref="cpuChartCanvas" style="min-height: 400px;"></canvas>
          </v-card-text>
        </v-card>

        <v-card v-if="info">
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
            <v-table density="compact">
              <thead>
                <tr>
                  <th class="text-left">Name</th>
                  <th class="text-right">Current</th>
                  <th class="text-right">High</th>
                  <th class="text-right">Critical</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="(temps, controller) in info.temperatures" :key="controller">
                  <tr :class="isDark ? 'bg-grey-darken-3' : 'bg-grey-lighten-4'">
                    <td colspan="4" class="font-weight-bold">{{ controller }}</td>
                  </tr>
                  <tr v-for="(temp, index) in temps" :key="`${controller}-${index}`">
                    <td class="pl-4">{{ temp.name }}</td>
                    <td class="text-right" :class="getTempColorClass(temp)">{{ temp.current }} °C</td>
                    <!-- Limits above 300 °C are not real: the driver reports an unset limit as a sentinel (an NVMe drive
                         shows 65261.85 °C, the value 65261850 m°C in hwmon). They are shown in grey, not hidden. -->
                    <template v-if="temp.high || temp.critical">
                      <td class="text-right" :class="{ 'text-grey': (temp.high || temp.critical) > 300 }">{{ temp.high || temp.critical }} °C</td>
                      <td class="text-right" :class="{ 'text-grey': (temp.critical || temp.high) > 300 }">{{ temp.critical || temp.high }} °C</td>
                    </template>
                    <template v-else>
                      <td class="text-right text-grey">N/A</td>
                      <td class="text-right text-grey">N/A</td>
                    </template>
                  </tr>
                </template>
              </tbody>
            </v-table>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="6" lg="4" v-if="info">
        <v-card class="mb-6">
          <v-card-title>Fans</v-card-title>
          <v-card-text>
            <div v-for="(fans, controller) in info.fans" :key="controller" class="mb-4">
              <div class="text-subtitle-1 font-weight-bold mb-2">{{ controller }}</div>
              <v-table density="compact">
                <tbody>
                  <tr v-for="(fan, index) in fans" :key="index">
                    <td class="d-flex align-center">
                      <v-icon
                        :color="fan.speed > 0 ? 'primary' : 'grey'"
                        class="mr-2"
                        :class="{ 'spin-icon': fan.speed > 0 }"
                      >
                        mdi-fan
                      </v-icon>
                      {{ formatFanName(fan.name) }}
                    </td>
                    <td class="text-right">{{ fan.speed }} RPM</td>
                  </tr>
                </tbody>
              </v-table>
            </div>
          </v-card-text>
        </v-card>

        <v-card class="mb-6">
          <v-card-title>Graphics</v-card-title>
          <v-card-text>
            <ul>
              <li v-for="(value, key) in gpu" :key="key">
                {{ gpuFeatureLabels[key] || key }}:
                <span :style="{color: gpuStatusColors[(gpuStatusLabels[value] || {color: 'red'}).color].value}">
                  {{ (gpuStatusLabels[value] || {label: 'Unknown'}).label }}
                </span>
              </li>
            </ul>
          </v-card-text>
        </v-card>

        <v-card v-if="info.disks" class="mb-6">
          <v-card-title>Storage</v-card-title>
          <v-card-text>
            <template v-for="disk in info.disks" :key="disk.name">
              <disk-item :disk="disk" :depth="0" />
            </template>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup>
import {api} from '@/api'
import { ref, onMounted, computed, defineComponent, h } from 'vue';
import { useTheme } from 'vuetify';
import { VProgressLinear, VChip } from 'vuetify/components';
import Chart from 'chart.js/auto';

const theme = useTheme();
const isDark = computed(() => theme.global.current.value.dark);

const info = ref(null);
const gpu = ref({});

// Graphics feature status of the viewer (chrome://gpu's list), labels and colours ported from Chromium's info_view
// Inspired by Chromium's chrome://gpu page (content/browser/resources/gpu/info_view.ts)
const gpuStatusColors = {
  green:  computed(() => isDark.value ? '#0F0' : '#080'),
  yellow: computed(() => isDark.value ? '#FF0' : '#880'),
  red:    computed(() => '#f00'),
}
const gpuFeatureLabels = {
  '2d_canvas': 'Canvas',
  'gpu_compositing': 'Compositing',
  'webgl': 'WebGL',
  'multisampling': 'WebGL multisampling',
  'texture_sharing': 'Texture Sharing',
  'video_decode': 'Video Decode',
  'rasterization': 'Rasterization',
  'opengl': 'OpenGL',
  'metal': 'Metal',
  'vulkan': 'Vulkan',
  'multiple_raster_threads': 'Multiple Raster Threads',
  'native_gpu_memory_buffers': 'Native GpuMemoryBuffers',
  'protected_video_decode': 'Hardware Protected Video Decode',
  'surface_control': 'Surface Control',
  'vpx_decode': 'VPx Video Decode',
  'canvas_oop_rasterization': 'Canvas out-of-process rasterization',
  'raw_draw': 'Raw Draw',
  'video_encode': 'Video Encode',
  'direct_rendering_display_compositor': 'Direct Rendering Display Compositor',
  'webgpu': 'WebGPU',
  'skia_graphite': 'Skia Graphite',
  'webnn': 'WebNN',
  'trees_in_viz': 'TreesInViz',
  'webgpu_on_vk_via_gl_interop': 'WebGPU interop',
}
const gpuStatusLabels = {
  'disabled_software': {label: 'Software only. Hardware acceleration disabled', color: 'yellow'},
  'disabled_off': {label: 'Disabled', color: 'red'},
  'disabled_off_ok': {label: 'Disabled', color: 'yellow'},
  'unavailable_software': {label: 'Software only, hardware acceleration unavailable', color: 'yellow'},
  'unavailable_off': {label: 'Unavailable', color: 'red'},
  'unavailable_off_ok': {label: 'Unavailable', color: 'yellow'},
  'enabled_readback': {label: 'Hardware accelerated but at reduced performance', color: 'yellow'},
  'enabled_force': {label: 'Hardware accelerated on all pages', color: 'green'},
  'enabled': {label: 'Hardware accelerated', color: 'green'},
  'enabled_on': {label: 'Enabled', color: 'green'},
  'enabled_force_on': {label: 'Force enabled', color: 'green'},
}
api.Settings.Display.gpu_feature_stats.on(data => {
  gpu.value = data.features;
}, {init: true})
const cpuChartCanvas = ref(null);
let cpuChart = null;

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

// Pushed by the server every few seconds to every logged-in client; there is no function to call for it
api.Settings.info.on(data => {
  info.value = data;
  updateChartData();
})

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
  // Index 0 is the aggregate total (/proc/stat's "cpu " line), not core 0 -
  // per-core entries ("cpu0", "cpu1", ...) start at index 1.
  cpuChart.data.labels = cpuData.map((_, index) => index === 0 ? 'Total' : `Core ${index - 1}`);
  const newDatasets = getDatasets(cpuData);

  cpuChart.data.datasets.forEach((dataset, index) => {
    dataset.data = newDatasets[index].data;
  });

  cpuChart.update();
};

const getDatasets = (cpuData) => {
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

const formatBytes = (bytes) => {
  if (bytes === 0) return '0 Bytes';
  if (!bytes) return '';
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

const formatFanName = (name) => {
  return /^\d+$/.test(name) ? `Fan ${name}` : name;
};

const getTempColorClass = (temp) => {
  const high = temp.high || temp.critical;
  const critical = temp.critical || temp.high;

  if (critical && temp.current > critical) return 'text-red';
  if (high && temp.current > high) return 'text-orange';
  return '';
};

const DiskItem = defineComponent({
  props: ['disk', 'depth'],
  setup(props) {
    return () => {
      const disk = props.disk;
      if (!disk.size) return null;

      const paddingLeft = `${props.depth * 1.5}rem`;

      return h('div', { class: 'd-flex flex-column' }, [
        h('div', { class: 'd-flex align-center py-2', style: { paddingLeft } }, [
          h('div', { class: 'mr-2' }, [
            h('span', { class: 'text-body-2 font-weight-bold' }, disk.name),
            disk.fstype ? h(VChip, { size: 'x-small', class: 'ml-2' }, { default: () => disk.fstype }) : null
          ]),
          h('div', { class: 'flex-grow-1' }, [
            disk.fsuse_perc != null ? [
              h(VProgressLinear, {
                modelValue: disk.fsuse_perc,
                height: 20,
                color: 'primary',
                rounded: true,
              }, {
                default: () => h('div', { class: 'd-flex justify-center align-center w-100 h-100' }, [
                    h('strong', { class: 'text-caption text-on-primary' },
                  `${formatBytes(disk.fsused)} / ${formatBytes(disk.size)}`)
                ])
              }),
              h('div', { class: 'text-caption text-medium-emphasis' }, disk.mountpoint)
            ] : h('div', { class: 'text-right text-caption' }, formatBytes(disk.size))
          ])
        ]),
        disk.children ? disk.children.map(child => h(DiskItem, { disk: child, depth: props.depth + 1 })) : null
      ]);
    };
  }
});

onMounted(() => {
  initializeChart();
});
</script>


<style scoped>
.text-h6 {
  margin-bottom: 1rem;
}
@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
.spin-icon {
  animation: spin 2s linear infinite;
}
</style>
