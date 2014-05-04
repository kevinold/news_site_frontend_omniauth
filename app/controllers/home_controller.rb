class HomeController < ApplicationController
  def index
    @stories = []
    if current_user
      @stories = Harvest.where(uid: current_user.uid).desc(:rank)
    end
  end
end
