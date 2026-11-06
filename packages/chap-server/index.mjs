// @brightbeamai/chap-server: the server a CHAP project runs. The desk, CHAP
// calls at POST /chap, the read API the desk uses, the analytics pages and
// an MCP endpoint for the agent, over the coordinator and its store, from a
// project's chap.config.json.

export {
  AGENT_MCP_TOOLS, DESK_DIR, bootstrap, chapHandler, createChapServer, hasProfile, listTasks, loadConfig, makeCoordinator,
  makeHandler, makeServer, openReviews, openStore, publicConfig, reconcileOidcProfile, reconcileSignedProfile, serve, start, storePath, taskEvidence,
  taskView,
} from "./lib/server.mjs";
export { isMain, usage, wantsHelp } from "./lib/main.mjs";
