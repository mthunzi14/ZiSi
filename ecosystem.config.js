module.exports = {
  apps: [
    {
      name: 'ZiSi-Core-Engine',
      script: '/root/ZiSi/venv/bin/python3',
      args: 'app/main.py',
      cwd: '/root/ZiSi',
      interpreter: 'none',
      autorestart: true,
      watch: false,
      max_memory_restart: '800M',
      env: { PYTHONPATH: '/root/ZiSi' }
    },
    {
      name: 'zisi-dashboard',
      script: 'dashboard/backend/server.js',
      cwd: '/root/ZiSi',
      autorestart: true,
      watch: false,
      env: { PORT: '5000', NODE_ENV: 'production' }
    }
  ]
};
