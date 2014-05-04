class Harvest
  include Mongoid::Document
  store_in collection: "ptwobrussell-harvest"
  field :title
  #field :source_type
  field :favorite_count
  field :tweet_created_at
  field :url
  field :summary
  field :source_image_url
  field :hostname
end
