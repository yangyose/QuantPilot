import { createRouter, createWebHistory } from 'vue-router'
import { getSetupStatus } from '@/api/setup'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/onboarding',
      name: 'onboarding',
      component: () => import('@/views/OnboardingView.vue'),
    },
    {
      path: '/',
      component: () => import('@/components/AppLayout.vue'),
      children: [
        { path: '', redirect: '/dashboard' },
        {
          path: 'dashboard',
          name: 'dashboard',
          component: () => import('@/views/DashboardView.vue'),
        },
        {
          path: 'signals',
          name: 'signals',
          component: () => import('@/views/SignalsView.vue'),
        },
        {
          path: 'positions',
          name: 'positions',
          component: () => import('@/views/PositionsView.vue'),
        },
        {
          path: 'factors',
          name: 'factors',
          component: () => import('@/views/FactorQualityView.vue'),
        },
        {
          path: 'reports',
          name: 'reports',
          component: () => import('@/views/ReportsView.vue'),
        },
        {
          path: 'backtest',
          name: 'backtest',
          component: () => import('@/views/BacktestView.vue'),
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/SettingsView.vue'),
        },
      ],
    },
  ],
})

// 路由守卫：
// 1) 未登录 → 强制 /login
// 2) 已登录但向导未完成 → 强制 /onboarding（避免直接刷新 /dashboard 绕过向导）
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isLoggedIn) {
    return { name: 'login' }
  }
  if (auth.isLoggedIn && to.name !== 'onboarding' && to.name !== 'login') {
    try {
      const status = await getSetupStatus()
      if (!status.completed) {
        return { name: 'onboarding' }
      }
    } catch {
      // setup API 调用失败不阻塞导航；OnboardingView 仍可手动访问
    }
  }
})

export default router
