class HomeController < ApplicationController
  def index
    @stories = Harvest.all.desc(:rank)
  end
end
