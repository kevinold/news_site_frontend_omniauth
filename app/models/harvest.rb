class Harvest
  include Mongoid::Document
  store_in collection: "ptwobrussell-harvest"
  field :title
  field :source_type
end
