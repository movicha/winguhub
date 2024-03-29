/* vim: set shiftwidth=2 tabstop=2 autoindent cindent expandtab filetype=javascript : */
/*
 * pdf2htmlEX.js : a simple demo UI for pdf2htmlEX
 *
 * handles UI events/actions/effects
 * Copyright 2012,2013 ClouidIO <info@clouidio.com>
 */

var pdf2htmlEX = (function(){
  var CSS_CLASS_NAMES = {
    page_frame       : 'pf',
    page_decoration  : 'pd',
    page_content_box : 'pc',
    page_data        : 'pi',
    background_image : 'bi',
    link             : 'l',
    __dummy__        : 'no comma'
  };

  var pdf2htmlEX = new Object();

  var EPS = 1e-6;
  var invert = function(ctm) {
    var det = ctm[0] * ctm[3] - ctm[1] * ctm[2];
    var ictm = new Array();
    ictm[0] = ctm[3] / det;
    ictm[1] = -ctm[1] / det;
    ictm[2] = -ctm[2] / det;
    ictm[3] = ctm[0] / det;
    ictm[4] = (ctm[2] * ctm[5] - ctm[3] * ctm[4]) / det;
    ictm[5] = (ctm[1] * ctm[4] - ctm[0] * ctm[5]) / det;
    return ictm;
  };
  var transform = function(ctm, pos) {
    return [ctm[0] * pos[0] + ctm[2] * pos[1] + ctm[4]
           ,ctm[1] * pos[0] + ctm[3] * pos[1] + ctm[5]];
  };
  var Page = function(page, container) {
    if(page == undefined) return;

    this.p = $(page);
    this.n = parseInt(this.p.attr('data-page-no'), 16);
    this.b = $('.'+CSS_CLASS_NAMES['page_content_box'], this.p);

    /*
     * scale ratios
     * 
     * default_r : the first one
     * set_r : last set 
     * cur_r : currently using
     */
    this.default_r = this.set_r = this.cur_r = this.p.height() / this.b.height();

    this.data = JSON.parse($($('.'+CSS_CLASS_NAMES['page_data'], this.p)[0]).attr('data-data'));

    this.ctm = this.data.ctm;
    this.ictm = invert(this.ctm);
    this.container = container;
  };
  $.extend(Page.prototype, {
    /* hide & show are for contents, the page frame is still there */
    hide : function(){
      this.b.removeClass('opened');
    },
    show : function(){
      if(Math.abs(this.set_r - this.cur_r) > EPS) {
        this.cur_r = this.set_r;
        this.b.css('transform', 'scale('+this.cur_r.toFixed(3)+')');
      }
      this.b.addClass('opened');
    },
    rescale : function(ratio, is_relative) {
      if(ratio == 0) {
        this.set_r = this.default_r;
      } else if (is_relative) {
        this.set_r *= ratio;
      } else {
        this.set_r = ratio;
      }

      /* wait for redraw */
      this.hide();

      this.p.height(this.b.height() * this.set_r);
      this.p.width(this.b.width() * this.set_r);
    },
    /* return if any part of this page is shown in the container */
    is_visible : function() {
      var off = this.position();
      return !((off[1] > this.height()) || (off[1] + this.container.height() < 0));
    },
    /* return if this page or any neighbor of it is visible */
    is_nearly_visible : function() {
      var off = this.position();
      /* I should use the height of the previous page or the next page here
       * but since they are not easily available, just use '*2', which should be a good estimate in most cases
       */
      return !((off[1] > this.height() * 2) || (off[1] + this.container.height() * 2 < 0));
    },
    /* return the coordinate of the top-left corner of container
     * in our cooridnate system
     */
    position : function () {
      var off = this.p.offset();
      var off_c = this.container.offset();
      return [off_c.left-off.left, off_c.top-off.top];
    },
    height : function() {
      return this.p.height();
    }
  });

  pdf2htmlEX.Viewer = function(config) {
    this.container_id = config['container_id'];
    this.sidebar_id = config['sidebar_id'];
    this.outline_id = config['outline_id'];
    this.page_urls = config['page_urls'];
    this.init_before_loading_content();

    var _ = this;
    $(function(){_.init_after_loading_content();});
  };

  $.extend(pdf2htmlEX.Viewer.prototype, {
    /* Constants */
    render_timeout : 100,
    scale_step : 0.9,

    init_before_loading_content : function() {
      /*hide all pages before loading, will reveal only visible ones later */
      this.pre_hide_pages();
    },
    init_after_loading_content : function() {
      this.sidebar = $('#'+this.sidebar_id);
      this.outline = $('#'+this.outline_id);
      this.container = $('#'+this.container_id);

      // Open the outline if nonempty
      if(this.outline.children().length > 0) { 
        this.sidebar.addClass('opened');
      }
      
      this.find_pages();

      // register schedule rendering
      var _ = this;
      this.container.scroll(function(){ _.schedule_render(); });

      //this.zoom_fixer();
      
      // handle links
      this.container.add(this.outline).on('click', '.'+CSS_CLASS_NAMES['link'], this, this.link_handler);

      // disable background image draging
      $('img', this.container).on('dragstart', function(e){return false;});

      this.render();

      // load split pages
      // has no effect if --split-pages is 0
      this.load_page(0);
    },
    find_pages : function() {
      var new_pages = new Array();
      var pl= $('.'+CSS_CLASS_NAMES['page_frame'], this.container);
      /* don't use for(..in..) */
      for(var i = 0, l = pl.length; i < l; ++i) {
        var p = new Page(pl[i], this.container);
        new_pages[p.n] = p;
      }
      this.pages = new_pages;
    },
    load_page : function(idx) {
      if(idx < this.page_urls.length){
        var _ = this;
        $.ajax({
          url: this.page_urls[idx],
          dataType: 'text'
        }).done(function(data){
          $('#'+_.container_id).append(data);
          _.find_pages();
          _.schedule_render();
          _.load_page(idx+1);
        });
      }
    },
    pre_hide_pages : function() {
      /* pages might have not been loaded yet, so add a CSS rule */
      var s = '@media screen{.'+CSS_CLASS_NAMES['page_content_box']+'{display:none;}}';
      var n = document.createElement('style');
      n.type = 'text/css';
      if (n.styleSheet) {
        n.styleSheet.cssText = s;
      } else {
        n.appendChild(document.createTextNode(s));
      }
      document.getElementsByTagName('head')[0].appendChild(n);
    },

    hide_pages : function() {
      var pl = this.pages;
      for(var i in pl)
        pl[i].hide();
    },

    render : function () {
      /* hide (positional) invisible pages */
      var pl = this.pages;
      for(var i in pl) {
        var p = pl[i];
        if(p.is_nearly_visible()){
          p.show();
        } else {
          p.hide();
        }
      }
    },

    schedule_render : function() {
      if(this.render_timer)
        clearTimeout(this.render_timer);

      var _ = this;
      this.render_timer = setTimeout(function () {
        _.render();
      }, this.render_timeout);
    },

    zoom_fixer : function () {
      /* 
       * When user try to zoom in/out using ctrl + +/- or mouse wheel
       * handle this and prevent the default behaviours
       *
       * Code credit to PDF.js
       */
      var _ = this;
      // Firefox specific event, so that we can prevent browser from zooming
      $(window).on('DOMMouseScroll', function(e) {
        if (e.ctrlKey) {
          e.preventDefault();
          _.rescale(Math.pow(_.scale_step, e.detail), true);
        }
      });

      $(window).on('keydown', function keydown(e) {
        if (e.ctrlKey || e.metaKey) {
          switch (e.keyCode) {
            case 61: // FF/Mac '='
            case 107: // FF '+' and '='
            case 187: // Chrome '+'
              _.rescale(1.0 / _.scale_step, true);
              break;
            case 173: // FF/Mac '-'
            case 109: // FF '-'
            case 189: // Chrome '-'
              _.rescale(_.scale_step, true);
              break;
            case 48: // '0'
              _.rescale(0, false);
              break;
            default:
              return;
          }
          e.preventDefault();
        }
      });
    },

    rescale : function (ratio, is_relative) {
      var pl = this.pages;
      for(var i in pl) {
        pl[i].rescale(ratio, is_relative);
      }

      this.schedule_render();
    },

    get_containing_page : function(obj) {
      /* get the page obj containing obj */
      var p = obj.closest('.'+CSS_CLASS_NAMES['page_frame'])[0];
      return p && this.pages[(new Page(p)).n];
    },

    link_handler : function (e) {
      var _ = e.data;
      var t = $(e.currentTarget);

      var cur_pos = [0,0];

      // cur_page might be undefined, e.g. from Outline
      var cur_page = _.get_containing_page(t);
      if(cur_page != undefined)
      {
        cur_pos = cur_page.position();
        //get the coordinates in default user system
        cur_pos = transform(cur_page.ictm, [cur_pos[0], cur_page.height()-cur_pos[1]]);
      }

      var detail_str = t.attr('data-dest-detail');
      if(detail_str == undefined) return;

      var ok = false;
      var detail = JSON.parse(detail_str);

      var target_page = _.pages[detail[0]];
      if(target_page == undefined) return;

      var pos = [0,0];
      var upside_down = true;
      // TODO: zoom
      // TODO: BBox
      switch(detail[1]) {
        case 'XYZ':
          pos = [(detail[2] == null) ? cur_pos[0] : detail[2]
                 ,(detail[3] == null) ? cur_pos[1] : detail[3]];
          ok = true;
          break;
        case 'Fit':
        case 'FitB':
          pos = [0,0];
          ok = true;
          break;
        case 'FitH':
        case 'FitBH':
          pos = [0, (detail[2] == null) ? cur_pos[1] : detail[2]]
          ok = true;
          break;
        case 'FitV':
        case 'FitBV':
          pos = [(detail[2] == null) ? cur_pos[0] : detail[2], 0];
          ok = true;
          break;
        case 'FitR':
          /* locate the top-left corner of the rectangle */
          pos = [detail[2], detail[5]];
          upside_down = false;
          ok = true;
          break;
        default:
          ok = false;
          break;
      }

      if(ok) {
        pos = transform(target_page.ctm, pos);
        if(upside_down) {
          pos[1] = target_page.height() - pos[1];
        }
        _.scroll_to(detail[0], pos);
        e.preventDefault();
      }
    }, 
    
    /* pos=[x,y], where (0,0) is the top-left corner */
    scroll_to : function(pageno, pos) {
      var target_page = this.pages[pageno];
      if(target_page == undefined) return;

      var cur_target_pos = target_page.position();

      this.container.scrollLeft(this.container.scrollLeft()-cur_target_pos[0]+pos[0]);
      this.container.scrollTop(this.container.scrollTop()-cur_target_pos[1]+pos[1]);
    },

    __last_member__ : 'no comma' /*,*/
  });

  return pdf2htmlEX;
})();
