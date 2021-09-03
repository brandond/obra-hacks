'use strict';

var baseTitle;

var pageViewModel = {
  // Page display state vars
  pageLocation: ko.observable(),
  pageTemplate: ko.observable(),
  pageContext: ko.observable(),
  activePanel: ko.observable(),
  // Specific data loaded from API to build template content
  upgradesPendingResults: ko.observable(),
  upgradesRecentResults: ko.observable(),
  searchResults: ko.observableArray(),
  personResults: ko.observable(),
  eventResults: ko.observable(),
  ranksResults: ko.observableArray(),
  yearEvents: ko.observable(),
  // Generic data loaded at all times
  upgradesPending: ko.observableArray(),
  upgradesRecent: ko.observableArray(),
  eventsRecent: ko.observableArray(),
  eventsYears: ko.observableArray(),
  // UI hacks
  searchSubmit: function(form){
    page('/search?' + $(form).serialize());
  },
  changeActivePanel: function(tabData, e){
    console.log('changeActivePanel', tabData, e);
    var $root = this;
    $root.activePanel(tabData.name);
    window.location.hash = '#' + tabData.name;
  },
  setActivePanel: function(tabData, element){
    var findFunc = function(){return false};
    var $root = this;
    switch($root.pageTemplate()) {
      case 'person':
      case 'upgrades':
        findFunc = function(){
          if (window.location.hash){
            if (window.location.hash == '#' + this.name){
              $root.activePanel(this.name);
              return false;
            }
          } else if (this.results.length > 0){
            $root.activePanel(this.name);
            page.replace(window.location.pathname + '#' + this.name, undefined, false, false);
            return false;
          }
        }
        break;
      case 'events':
        findFunc = function(){
          if (window.location.hash){
            if (window.location.hash == '#' + this.name){
              $root.activePanel(this.name);
              return false;
            }
          } else if (this.events.length > 0){
            $root.activePanel(this.name);
            page.replace(window.location.pathname + '#' + this.name, undefined, false, false);
            return false;
          }
        }
        break;
      case 'ranks':
        findFunc = function(){
          if (window.location.hash){
            if (window.location.hash == '#' + this.name){
              $root.activePanel(this.name);
              return false;
            }
          } else if (this.ranks.length > 0){
            $root.activePanel(this.name);
            page.replace(window.location.pathname + '#' + this.name, undefined, false, false);
            return false;
          }
        }
        break;
    }
    $.each(tabData, findFunc);
  }
};

function switchPage(context, next){
  console.log('switchPage to ' + context.path , arguments);
  $('.navbar-collapse').collapse('hide');
  pageViewModel.pageContext(context);
  pageViewModel.pageLocation(context.pathname);
  pageViewModel.pageTemplate(context.pathname.split('/')[1] || 'index');
};

function scrollToHash(){
  console.log('scrollToHash', $(window.location.hash)[0]);
  if ($(window.location.hash).length){
    $(window.location.hash)[0].scrollIntoView({ behavior: 'smooth', block: 'start', inline: 'start'});
    return;
  }
  window.scrollTo(0, 0);
}

function doIndex(context, next){
  $.get('/api/v1/events/recent/', function(events){
    pageViewModel.eventsRecent(events);
  });
  $.get('/api/v1/upgrades/pending/top/', function(upgrades){
    pageViewModel.upgradesPending(upgrades);
  });
  $.get('/api/v1/upgrades/recent/top/', function(upgrades){
    pageViewModel.upgradesRecent(upgrades);
  });
  document.title = baseTitle;
  next();
}

function doUpgrades(context, next){
  if (context.params.type == 'pending'){
    $.get('/api/v1/upgrades/pending/', function(results){
      pageViewModel.upgradesPendingResults(results);
    });
  } else if (context.params.type == 'recent'){
    $.get('/api/v1/upgrades/recent/', function(results){
      pageViewModel.upgradesRecentResults(results);
    });
  }
  document.title = baseTitle + ': Upgrades';
  next();
}

function doEvent(context, next){
  pageViewModel.eventResults(undefined);

  $.get('/api/v1/results/event/' + context.params.id, function(results){
    pageViewModel.eventResults(results);
    document.title = baseTitle + ': Results: ' + results.year + ': ' +  results.name;
  }).fail(function(){
    page('/events');
  });

  next();
};

function doEvents(context, next){
  $.get('/api/v1/events/years/', function(years){
    pageViewModel.eventsYears(years);
  }).fail(function(){
    page('/');
  });

  document.title = baseTitle + ': Events';

  ko.when(function(){
    return pageViewModel.eventsYears().length != 0;
  }, function(){
    page('/events/' + pageViewModel.eventsYears()[0]);
  });
};

function doEventsYear(context, next){
  pageViewModel.yearEvents(undefined);

  $.get('/api/v1/events/years/' + context.params.year + '/', function(results){
    pageViewModel.yearEvents(results);
  }).fail(function(){
    page('/events');
  });

  $.get('/api/v1/events/years/', function(years){
    pageViewModel.eventsYears(years);
  });

  document.title = baseTitle + ': Events: ' + context.params.year;
  next();
};

function doPerson(context, next){
  pageViewModel.personResults(undefined);
  $.get('/api/v1/results/person/' + context.params.id, function(results){
    pageViewModel.personResults(results);
    document.title = baseTitle + ': Results: ' + results.name;
  }).fail(function(){
    page('/');
  });
  next();
};

function doSearch(context, next){
  pageViewModel.searchResults([]);
  $.get('/api/v1/people/?' + context.querystring, function(results){
    pageViewModel.searchResults(results);
  })
  document.title = baseTitle + ': Search';
  next();
};

function doRanks(context, next){
  pageViewModel.ranksResults([]);
  $.get('/api/v1/ranks/', function(results){
    pageViewModel.ranksResults(results);
    document.title = baseTitle + ': Ranks';
  }).fail(function(){
    page('/');
  });
  next();
};

function doNotifications(context, next){
  document.title = baseTitle + ': Notifications';
  next();
}

window.addEventListener('load', function() {
  baseTitle = document.title;
  $.ajaxSetup({traditional: true});

  page('/', doIndex);
  page('/search*', doSearch);
  page('/events', doEvents);
  page('/events/:year', doEventsYear);
  page('/event/:id', doEvent);
  page('/ranks', doRanks);
  page('/notifications', doNotifications);
  page('/person/:id', doPerson);
  page('/upgrades/:type', doUpgrades);
  page('*', switchPage);

  ko.options.deferUpdates = true;
  ko.applyBindings(pageViewModel);
  page({'popstate': false, 'hashchange': false});

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/js/serviceworker.js').then(console.log);
  }
});

window.addEventListener('popstate', (e) => {
  // The built-in popstate hook doesn't detect change and just updates all the time, including on hash changes.
  if (e.target.location.pathname == pageViewModel.pageLocation()){
    pageViewModel.activePanel(e.target.location.hash.substr(1));
  } else {
    page.replace(e.target.location.pathname, e.state);
  }
});
