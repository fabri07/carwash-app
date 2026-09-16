import "@testing-library/jest-dom";

// jsdom doesn't implement scrollIntoView. Los tests con `@jest-environment node`
// (middleware, service worker) no tienen `Element`.
if (typeof Element !== "undefined") {
  Element.prototype.scrollIntoView = jest.fn();
}

// Sentry no tiene nada que hacer en los tests; se observa como mock.
jest.mock("@sentry/nextjs", () => ({
  addBreadcrumb: jest.fn(),
  captureException: jest.fn(),
  setUser: jest.fn(),
  setTag: jest.fn(),
}));
