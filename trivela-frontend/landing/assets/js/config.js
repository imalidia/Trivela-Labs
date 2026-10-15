/* Deployment settings. Public: nothing secret belongs here (API keys live on the servers, never in the site).
   engineUrl: the model server's address, e.g. 'https://engine.trivela.app'. Set it once when the backend is hosted;
   visitors then never see a server box. Left empty, the upload page shows a box to paste one (for testing). */
window.TRIVELA_CONFIG = {
  engineUrl: 'https://cornwall-madrid-massachusetts-manchester.trycloudflare.com',
};
