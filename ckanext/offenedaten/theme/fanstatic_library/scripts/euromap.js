
this.ckan.module('euromap', function($) {
	return {
		options: {
			results: {},
			baseurl: 'organization',
			homepage: false
		},
		/* The definitive list if categories is defined, here. It should match the set defined in metautils.py. */
    categories: ['Bevölkerung', 'Bildung und Wissenschaft', 'Geographie, Geologie und Geobasisdaten', 'Gesetze und Justiz', 'Gesundheit', 'Infrastruktur, Bauen und Wohnen', 'Kultur, Freizeit, Sport, Tourismus', 'Politik und Wahlen', 'Soziales', 'Transport und Verkehr', 'Umwelt und Klima', 'Verbraucherschutz', 'Öffentliche Verwaltung, Haushalt und Steuern', 'Wirtschaft und Arbeit', 'Sonstiges', 'Noch nicht kategorisiert'],
    MB_URL: 'http://{s}.tiles.mapbox.com/v3/{id}/{z}/{x}/{y}.png',
		initialize: function() {  
			var linecolor = '#000000'
			var options = {};

			var southWest = new L.latLng(46.10370875598026, 3.2299804687499996);
      var northEast = new L.latLng(55.7765730186677, 17.29248046875);
      var bounds = new L.latLngBounds(southWest, northEast);

			if (this.options.homepage) {
				options.dragging = options.touchZoom = options.scrollWheelZoom = options.doubleClickZoom = options.zoomControl = false;
				options.minZoom = options.maxZoom = 3.2;
				linecolor = '#A8BADB';
			} else {
				options.minZoom = 6;
				options.maxZoom = 12;
				options.maxBounds = bounds;
				options.zoomControl = false;
			}

			var map = new L.Map(this.el.prop('id'), options);
			var baseurl = this.options.baseurl;
			map.setView([51.358061573190916, 10.810546875], 6);
			
			L.tileLayer(this.MB_URL, {
      attribution: 'Map data &copy; <a href="http://openstreetmap.org">OpenStreetMap</a> contributors, ' +
      '<a href="http://creativecommons.org/licenses/by-sa/2.0/">CC-BY-SA</a>, ' +
      'Imagery © <a href="http://mapbox.com">Mapbox</a>',
      id: 'stefanw.map-4pdqtryz'
      }).addTo(map);
      
      /*$.getJSON('data/notgermany.geojson', function(feature){
        new L.geoJson(feature, {
          style: {
            color: '#ddd',
            fillColor: '#ddd',
            weight: 1,
            opacity: 0.0,
            fillOpacity: 0.8
          }
        }).addTo(this.map);
      });*/
      
      _.each(this.options.results, function(d) {
        _.each(d.extras, function(e) {
          d[e.key] = e.value;
        });        
        var gicon = L.MakiMarkers.icon({icon: "polling-place", color: "#0c0", size: "s"});
        var bicon = L.MakiMarkers.icon({icon: "polling-place", color: "#665", size: "s"});
        var lat = parseFloat(d.latitude, 10);
        var lon = parseFloat(d.longitude, 10);
        if (!d.city_type) d.city_type = '';
        if ((d.city_type.toLowerCase().indexOf('stadt') != -1) || (d.city_type == '')) {
          //It's a 'city'
          var marker = L.marker([lat, lon], {icon: gicon});
        }
        else {
          var marker = L.marker([lat, lon], {icon: bicon});
        }
        //Get the city content, and if it exists, add it to the map
        var cityslug = d.name;

        var emailContent = "";
        if ($.trim(d.contact_email).length > 0) emailContent = "<li>Kontakt: <a href=\"mailto:"+d.contact_email+"\">"+d.contact_email+"</a></li>";
        var opendataportal = "";
        if ($.trim(d.open_data_portal).length > 0) opendataportal = "<br>Datenkatalog: <a href=\""+d.open_data_portal+"\">"+d.open_data_portal+"</a>";
        var portal = "";
        if ($.trim(d.url).length > 0) portal = 'Portal: <a href=\"' + d.url + '\">' + d.url + '</a>';
        //TODO get modified
        marker.bindPopup('<h2><a href=\"' + baseurl + '/' + d.name + '\">' + d.title + '</a></h2><ul><li>' + d.package_count + ' Datensätze</li>' + emailContent + '</ul>' + portal + opendataportal);
        marker.bindLabel(d.title, {
          className: 'labelClass'
        });
        marker.addTo(map);
      });
		},
	};
});