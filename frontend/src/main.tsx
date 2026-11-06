import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, Suspense, lazy, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, type RouteObject } from "react-router-dom";
import "./styles/index.css";
import "leaflet/dist/leaflet.css";
import "./styles/map.css";       // Leaflet first, so the map styles override it; markers also show outside maps
import { ApiError } from "./api/client";
import { Shell, type RouteHandle } from "./app/Shell";
import { NotFound } from "./pages/NotFound";
import { Toaster } from "./ui/Toast";
import { TooltipProvider } from "./ui/Tooltip";

// Each page loads on its first visit; Live (with the map) is the heaviest.
const Live = lazy(() => import("./pages/Live"));
const Fleet = lazy(() => import("./pages/Fleet"));
const Hospitals = lazy(() => import("./pages/Hospitals"));
const System = lazy(() => import("./pages/System"));
const Report = lazy(() => import("./pages/Report"));
const Incidents = lazy(() => import("./pages/Incidents"));
const Incident = lazy(() => import("./pages/Incident"));
const History = lazy(() => import("./pages/History"));
const Scenarios = lazy(() => import("./pages/Scenarios"));
const lazyPage = (el: ReactNode) => <Suspense fallback={null}>{el}</Suspense>;

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000,
      refetchOnWindowFocus: false,
      // 4xx is an answer, not a hiccup: do not retry it
      retry: (n, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && n < 2,
    },
  },
});

const page = (title: string, element: ReactNode, path: string): RouteObject => ({ path, element, handle: { title } satisfies RouteHandle });

const router = createBrowserRouter([
  {
    element: <Shell />,
    children: [
      page("Live", lazyPage(<Live />), "/"),
      page("Report emergency", lazyPage(<Report />), "/report"),
      page("Incidents", lazyPage(<Incidents />), "/incidents"),
      page("Incident", lazyPage(<Incident />), "/incidents/:id"),
      page("History", lazyPage(<History />), "/history"),
      page("Scenarios", lazyPage(<Scenarios />), "/scenarios"),
      page("Fleet", lazyPage(<Fleet />), "/fleet"),
      page("Hospitals", lazyPage(<Hospitals />), "/hospitals"),
      page("System", lazyPage(<System />), "/system"),
      page("Not found", <NotFound />, "*"),
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <RouterProvider router={router} />
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
);
