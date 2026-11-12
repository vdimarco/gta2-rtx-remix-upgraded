[out:json][timeout:180];
// Plateau-Mont-Royal with a margin: avenue du Parc to past Papineau,
// Sherbrooke to past Saint-Joseph.
(
  way["highway"](45.5080,-73.6000,45.5400,-73.5580);
  way["building"](45.5080,-73.6000,45.5400,-73.5580);
  relation["building"](45.5080,-73.6000,45.5400,-73.5580);
  way["leisure"~"park|garden|playground|pitch"](45.5080,-73.6000,45.5400,-73.5580);
  way["natural"~"water|wood|scrub|grassland"](45.5080,-73.6000,45.5400,-73.5580);
  way["landuse"~"grass|recreation_ground|cemetery"](45.5080,-73.6000,45.5400,-73.5580);
  way["amenity"~"place_of_worship|parking"](45.5080,-73.6000,45.5400,-73.5580);
  relation["natural"="water"](45.5080,-73.6000,45.5400,-73.5580);
  relation["leisure"~"park|garden"](45.5080,-73.6000,45.5400,-73.5580);
  relation["landuse"~"grass|recreation_ground"](45.5080,-73.6000,45.5400,-73.5580);
  relation["highway"="pedestrian"](45.5080,-73.6000,45.5400,-73.5580);
  node["railway"="subway_entrance"](45.5080,-73.6000,45.5400,-73.5580);
);
out body;
>;
out skel qt;
