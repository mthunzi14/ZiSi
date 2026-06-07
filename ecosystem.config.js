module.exports = {
  apps: [
    {
      name: 'zisi-dashboard',
      script: 'presentation/dashboard/backend/server.js',
      cwd: '/root/ZiSi',
      autorestart: true,
      watch: false,
      env: { PORT: '5000', NODE_ENV: 'production' }
    }
  ]
};
