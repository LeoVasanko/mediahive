import { createRouter, createWebHistory } from "vue-router"
import { defineComponent, h } from "vue"

// Empty component - App.vue handles all rendering based on route meta
const EmptyRouteComponent = defineComponent({
  render() {
    return h("div")
  },
})

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior() {
    // Always scroll to top on navigation
    return { top: 0 }
  },
  routes: [
    {
      path: "/",
      redirect: "/movies",
    },
    {
      path: "/movies",
      name: "movies",
      component: EmptyRouteComponent,
      meta: { view: "movies" },
    },
    {
      path: "/movies/:id",
      name: "movie-detail",
      component: EmptyRouteComponent,
      meta: { view: "movies" },
    },
    {
      path: "/search/:term",
      name: "search",
      component: EmptyRouteComponent,
    },
    {
      path: "/series",
      name: "series",
      component: EmptyRouteComponent,
      meta: { view: "series" },
    },
    {
      path: "/series/:id",
      name: "series-detail",
      component: EmptyRouteComponent,
      meta: { view: "series" },
    },
  ],
})

export default router
